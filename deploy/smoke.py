"""Post-deployment checks through the actual web origin. Never accepts API Keys."""
from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid


class SmokeFailure(RuntimeError):
    pass


def run(base_url: str, *, expected_release: str | None = None, plan: bool = False, opener=urlopen):
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise SmokeFailure("Use an HTTP(S) origin without a path, query or fragment.")
    origin = base_url.rstrip("/")

    def fetch(path, *, method="GET", token=None, body=None, expected=200):
        headers = {"Accept": "application/json"}
        if token:
            headers["X-Demo-Token"] = token
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(origin + path, data=data, headers=headers, method=method)
        try:
            response = opener(request, timeout=180 if plan else 15)
        except HTTPError as error:
            response = error
        if response.status != expected:
            response.close()
            raise SmokeFailure(f"Unexpected HTTP status at {path}: expected {expected}, got {response.status}")
        return response

    def read_json(path, **options):
        with fetch(path, **options) as response:
            return json.load(response)

    with fetch("/") as response:
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise SmokeFailure("Web origin did not serve an HTML application.")
    version = read_json("/version.json").get("release")
    runtime = read_json("/api/config/runtime")["data"]
    if not runtime.get("public_demo"):
        raise SmokeFailure("Public demo boundary is not enabled. Do not open this origin to visitors.")
    if not version or version != runtime.get("release"):
        raise SmokeFailure("Frontend and backend release identifiers differ.")
    if expected_release and version != expected_release:
        raise SmokeFailure("Running release differs from the requested release.")
    with fetch("/api/sessions", expected=401):
        pass
    tokens = [read_json("/api/demo/visitors", method="POST", expected=201)["data"]["token"] for _ in range(2)]
    if tokens[0] == tokens[1]:
        raise SmokeFailure("Visitor capabilities are not distinct.")
    created = []
    try:
        session = read_json("/api/sessions", method="POST", token=tokens[0], expected=201)["data"]["session_id"]
        created.append((session, tokens[0]))
        with fetch(f"/api/sessions/{session}", token=tokens[1], expected=404):
            pass
        examples = read_json("/api/demo/showcases", token=tokens[0])["data"]["examples"]
        if len(examples) != 4:
            raise SmokeFailure("Read-only showcase package is missing.")
        if plan:
            payload = {"request_id": "deploy-smoke-" + uuid.uuid4().hex, "content": "明天下午出去玩"}
            with fetch(f"/api/sessions/{session}/messages/stream", method="POST", token=tokens[0], body=payload) as response:
                if "text/event-stream" not in response.headers.get("Content-Type", ""):
                    raise SmokeFailure("Planning endpoint did not return SSE.")
                event, data, result, progress = "", [], None, 0
                for raw in response:
                    line = raw.decode("utf-8").rstrip("\r\n")
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].strip())
                    elif not line and data:
                        payload = json.loads("\n".join(data))
                        if event == "error":
                            raise SmokeFailure("Planning stream reported an application error.")
                        if event == "result":
                            result = payload["data"]
                        if event == "progress":
                            progress += 1
                        event, data = "", []
                if not progress or not result or not result.get("plans"):
                    raise SmokeFailure("Planning smoke did not return both progress and usable plans.")
    finally:
        for session, token in created:
            with fetch(f"/api/sessions/{session}", method="DELETE", token=token):
                pass
    print(f"PASS: web + public auth + visitor isolation + examples; release={version}" + ("; SSE planning passed" if plan else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--expected-release")
    parser.add_argument("--plan", action="store_true", help="Also execute one real planning turn (uses configured site providers).")
    args = parser.parse_args()
    try:
        run(args.base_url, expected_release=args.expected_release, plan=args.plan)
    except Exception as error:
        # Do not dump response bodies, credentials or provider diagnostics.
        message = str(error) if isinstance(error, SmokeFailure) else type(error).__name__
        raise SystemExit("FAIL: " + message) from None


if __name__ == "__main__":
    main()
