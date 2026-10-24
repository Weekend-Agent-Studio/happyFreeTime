import io
from pathlib import Path
import sqlite3
import tarfile
from urllib.parse import urlsplit

import pytest
import yaml
from starlette.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from deploy.backup_volume import archive_volume
from deploy.smoke import SmokeFailure, run
from app.api.public_demo import PublicDemo
from tests.test_public_demo import make_app


ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_public_boundary_private_backend_and_data_separate():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    api, web = config["services"]["api"], config["services"]["web"]
    assert "ports" not in api and "network_mode" not in api
    assert api["environment"]["HFT_PUBLIC_DEMO"] == "true"
    assert api["environment"]["LLM_API"] == ""
    assert api["environment"]["HFT_DATABASE_PATH"].startswith("/var/lib/happyfreetime/")
    assert api["volumes"] == ["data:/var/lib/happyfreetime"]
    assert config["volumes"]["data"]["name"] == "happyfreetime-data"
    assert all("127.0.0.1" in port for port in web["ports"])
    assert api["command"][api["command"].index("--workers") + 1] == "1"
    assert api["command"][api["command"].index("--forwarded-allow-ips") + 1] != "*"
    assert api["read_only"] and web["read_only"]
    assert web["depends_on"]["api"]["condition"] == "service_healthy"
    tls = yaml.safe_load((ROOT / "compose.https.yaml").read_text(encoding="utf-8"))
    assert all(mount["read_only"] and not mount["bind"]["create_host_path"] for mount in tls["services"]["web"]["volumes"])


def test_proxy_overwrites_untrusted_headers_and_does_not_buffer_or_replay_sse():
    proxy = (ROOT / "deploy/nginx/proxy.conf").read_text()
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in proxy
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in proxy
    assert "$http_x_forwarded_proto" not in proxy and "$proxy_add_x_forwarded_for" not in proxy
    assert "proxy_buffering off;" in proxy and "proxy_next_upstream off;" in proxy
    nginx = (ROOT / "deploy/nginx/nginx.conf").read_text()
    assert "$request_body" not in nginx and "$http_authorization" not in nginx and "$request_uri" not in nginx
    assert "client_max_body_size 16k;" in nginx


def test_image_context_excludes_private_files_and_images_do_not_copy_the_repository():
    patterns = [line for line in (ROOT / ".dockerignore").read_text().splitlines() if line and not line.startswith("#")]
    assert patterns[0] == "**"
    assert "**/.env" in patterns and "**/*.db" in patterns and "**/*.pem" in patterns
    assert "!deploy/runtime.env" not in patterns and "!deploy/certs/**" not in patterns
    for name in ("backend.Dockerfile", "web.Dockerfile"):
        text = (ROOT / "deploy" / name).read_text()
        assert "COPY . " not in text and "COPY ./ " not in text
        assert "USER " in text
    web = (ROOT / "deploy/web.Dockerfile").read_text()
    assert "VITE_PUBLIC_DEMO=true" in web and "pnpm install --frozen-lockfile" in web
    assert "!frontend/pnpm-workspace.yaml" in patterns
    assert "COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./" in web
    policy = yaml.safe_load((ROOT / "frontend/pnpm-workspace.yaml").read_text())
    assert policy["allowBuilds"] == {"esbuild": True}


def test_stopped_volume_snapshot_preserves_sqlite_and_sidecars(tmp_path):
    source = tmp_path / "volume"
    source.mkdir()
    with sqlite3.connect(source / "public_demo.db") as database:
        database.execute("create table sample(value text)")
        database.execute("insert into sample values ('private maintenance data')")
    (source / "public_demo.db-wal").write_bytes(b"sidecar-fixture")
    archive = archive_volume(source, tmp_path / "backups")
    with tarfile.open(archive) as snapshot:
        assert snapshot.extractfile("data/public_demo.db-wal").read() == b"sidecar-fixture"
        restored = tmp_path / "restored.db"
        restored.write_bytes(snapshot.extractfile("data/public_demo.db").read())
    with sqlite3.connect(restored) as database:
        assert database.execute("select value from sample").fetchone()[0] == "private maintenance data"
    assert archive_volume(source, tmp_path / "backups") != archive
    with pytest.raises(ValueError):
        archive_volume(source, source / "unsafe-backup-location")


def test_deployment_smoke_exercises_real_public_http_and_sse(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HFT_RELEASE", "smoke-test")
    app = make_app(tmp_path / "smoke.db", PublicDemo())

    class Response(io.BytesIO):
        def __init__(self, response):
            super().__init__(response.content)
            self.status, self.headers = response.status_code, response.headers

    with TestClient(app) as client:
        def opener(request, timeout):
            parsed = urlsplit(request.full_url)
            # Nginx serves these static resources, outside the API capability gate.
            if parsed.path in ("/", "/version.json"):
                static = HTMLResponse("<html>public frontend fixture</html>") if parsed.path == "/" else JSONResponse({"release": "smoke-test"})
                static.content = static.body
                return Response(static)
            return Response(client.request(request.get_method(), parsed.path, content=request.data, headers=dict(request.header_items())))
        run("http://127.0.0.1:8080", expected_release="smoke-test", plan=True, opener=opener)
        assert "SSE planning passed" in capsys.readouterr().out
        with pytest.raises(SmokeFailure, match="requested release"):
            run("http://127.0.0.1:8080", expected_release="wrong-release", opener=opener)


def test_smoke_rejects_path_based_origins_before_any_request():
    with pytest.raises(SmokeFailure):
        run("https://example.test/subdirectory")
