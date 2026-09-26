import unittest
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.orchestration.entry_graph import default_environment_provider
from app.providers.web_map import AmapWebMapProvider, build_web_map_provider
from app.services.demo_router import DemoRouter


class WebMapProviderContractTest(unittest.TestCase):
    def test_browser_credentials_must_be_configured_as_a_pair(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "AMAP_JS_SECURITY_CODE"):
            build_web_map_provider(
                js_api_key="public-js-key",
                security_code=None,
            )

    def test_browser_config_hides_security_code_and_proxy_adds_it_upstream(self) -> None:
        urls: list[str] = []

        def fetch(url: str) -> tuple[int, str, bytes]:
            urls.append(url)
            return 200, "application/json", b'{"status":"1"}'

        provider = AmapWebMapProvider(
            js_api_key="public-js-key",
            security_code="private-security-code",
            fetch=fetch,
        )

        config = provider.public_config()
        response = provider.proxy(
            "v4/map/styles",
            [("key", "public-js-key"), ("jscode", "attacker-value")],
        )

        self.assertEqual(config.provider, "amap")
        self.assertEqual(config.js_api_key, "public-js-key")
        self.assertNotIn("private-security-code", config.model_dump_json())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'{"status":"1"}')
        parsed = urlparse(urls[0])
        self.assertEqual(parsed.netloc, "webapi.amap.com")
        self.assertEqual(
            parse_qs(parsed.query)["jscode"],
            ["private-security-code"],
        )

    def test_api_exposes_public_config_and_proxies_without_leaking_security_code(
        self,
    ) -> None:
        urls: list[str] = []

        def fetch(url: str) -> tuple[int, str, bytes]:
            urls.append(url)
            return 200, "application/json", b'{"status":"1"}'

        provider = AmapWebMapProvider(
            js_api_key="public-js-key",
            security_code="private-security-code",
            fetch=fetch,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(
                database_path=Path(temp_dir) / "api.db",
                router=DemoRouter(),
                environment_provider=default_environment_provider,
                web_map_provider=provider,
            )
            with TestClient(app) as client:
                config = client.get("/api/config/map")
                proxied = client.get(
                    "/_AMapService/v3/weather/weatherInfo",
                    params={"key": "public-js-key", "city": "110000"},
                )

        self.assertEqual(config.status_code, 200, config.text)
        self.assertEqual(config.json()["data"]["provider"], "amap")
        self.assertEqual(config.json()["data"]["js_api_key"], "public-js-key")
        self.assertEqual(
            config.json()["data"]["service_host_path"],
            "/_AMapService",
        )
        self.assertNotIn("private-security-code", config.text)
        self.assertEqual(proxied.status_code, 200, proxied.text)
        self.assertEqual(proxied.json(), {"status": "1"})
        self.assertEqual(urlparse(urls[0]).netloc, "restapi.amap.com")


if __name__ == "__main__":
    unittest.main()
