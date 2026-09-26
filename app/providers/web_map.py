"""Browser map providers and their public, credential-safe configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal, Protocol
from urllib.parse import urlencode
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict


class WebMapConfig(BaseModel):
    """Configuration safe to expose to the browser."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    provider: Literal["none", "amap"]
    js_api_key: str | None = None
    version: str | None = None
    service_host_path: str | None = None


class WebMapProvider(Protocol):
    def public_config(self) -> WebMapConfig:
        ...

    def proxy(
        self,
        path: str,
        query_items: list[tuple[str, str]],
    ) -> "WebMapProxyResponse":
        ...


@dataclass(frozen=True)
class WebMapProxyResponse:
    status_code: int
    content_type: str
    content: bytes


class DisabledWebMapProvider:
    def public_config(self) -> WebMapConfig:
        return WebMapConfig(enabled=False, provider="none")

    def proxy(
        self,
        path: str,
        query_items: list[tuple[str, str]],
    ) -> WebMapProxyResponse:
        raise LookupError("browser map provider is not configured")


class AmapWebMapProvider:
    """Keep Amap's securityJsCode server-side while exposing the public JS key."""

    _ALLOWED_PATH = re.compile(r"^v[345]/[A-Za-z0-9_./-]+$")

    def __init__(
        self,
        *,
        js_api_key: str,
        security_code: str,
        fetch: Callable[[str], tuple[int, str, bytes]] | None = None,
    ) -> None:
        if not js_api_key or not security_code:
            raise ValueError("Amap browser map requires a JS key and security code")
        self._js_api_key = js_api_key
        self._security_code = security_code
        self._fetch = fetch or self._default_fetch

    def public_config(self) -> WebMapConfig:
        return WebMapConfig(
            enabled=True,
            provider="amap",
            js_api_key=self._js_api_key,
            version="2.0",
            service_host_path="/_AMapService",
        )

    def proxy(
        self,
        path: str,
        query_items: list[tuple[str, str]],
    ) -> WebMapProxyResponse:
        normalized_path = path.lstrip("/")
        if not self._ALLOWED_PATH.fullmatch(normalized_path):
            raise ValueError("unsupported Amap proxy path")
        safe_query = [
            (key, value)
            for key, value in query_items
            if key.casefold() != "jscode"
        ]
        safe_query.append(("jscode", self._security_code))
        host = (
            "webapi.amap.com"
            if normalized_path.startswith("v4/map/styles")
            else "restapi.amap.com"
        )
        url = f"https://{host}/{normalized_path}?{urlencode(safe_query)}"
        try:
            status_code, content_type, content = self._fetch(url)
        except Exception as error:
            raise RuntimeError("Amap browser map proxy request failed") from error
        return WebMapProxyResponse(
            status_code=status_code,
            content_type=content_type,
            content=content,
        )

    @staticmethod
    def _default_fetch(url: str) -> tuple[int, str, bytes]:
        with urlopen(url, timeout=5) as response:  # noqa: S310 - fixed Amap hosts
            return (
                response.status,
                response.headers.get_content_type(),
                response.read(),
            )


def build_web_map_provider(
    *,
    js_api_key: str | None,
    security_code: str | None,
) -> WebMapProvider:
    if bool(js_api_key) != bool(security_code):
        missing = "AMAP_JS_SECURITY_CODE" if js_api_key else "AMAP_JS_API_KEY"
        raise RuntimeError(
            f"{missing} is required when configuring the Amap browser map"
        )
    if not js_api_key or not security_code:
        return DisabledWebMapProvider()
    return AmapWebMapProvider(
        js_api_key=js_api_key,
        security_code=security_code,
    )
