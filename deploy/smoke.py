"""Post-deployment checks through the actual web origin. Never accepts API Keys."""
from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, urlopen
import uuid


class SmokeFailure(RuntimeError):
    pass


def run(base_url: str, *, expected_release: str | None = None, plan: bool = False, require_hybrid: bool = False, require_amap: bool = False, opener=urlopen):
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise SmokeFailure("Use an HTTP(S) origin without a path, query or fragment.")
    origin = base_url.rstrip("/")
    plan = plan or require_hybrid or require_amap

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
    if require_hybrid and (runtime.get("retrieval", {}).get("mode") != "hybrid" or not runtime.get("retrieval", {}).get("prewarmed")):
        raise SmokeFailure("Hybrid retrieval has not passed startup prewarm.")
    with fetch("/api/sessions", expected=401):
        pass
    tokens = [read_json("/api/demo/visitors", method="POST", expected=201)["data"]["token"] for _ in range(2)]
    if tokens[0] == tokens[1]:
        raise SmokeFailure("Visitor capabilities are not distinct.")
    created = []
    try:
        if require_amap:
            config = read_json("/api/config/map", token=tokens[0])["data"]
            places = read_json("/api/departure-locations/search?" + urlencode({"q": "国贸"}), token=tokens[0])["data"]
            if not config.get("enabled") or config.get("provider") != "amap" or places.get("source") not in {"amap_live", "cache"} or places.get("mode") not in {"live", "record"} or places.get("degraded") or not places.get("results"):
                raise SmokeFailure("Amap map configuration or live departure search is unavailable.")
        session = read_json("/api/sessions", method="POST", token=tokens[0], expected=201)["data"]["session_id"]
        created.append((session, tokens[0]))
        with fetch(f"/api/sessions/{session}", token=tokens[1], expected=404):
            pass
        examples = read_json("/api/demo/showcases", token=tokens[0])["data"]["examples"]
        if {example.get("id") for example in examples} != {"plan", "replace", "recovery"}:
            raise SmokeFailure("Read-only showcase package is missing.")
        if plan:
            payload = {"request_id": "deploy-smoke-" + uuid.uuid4().hex, "content": "明天下午出去玩，想安静一点" if require_hybrid else "明天下午出去玩"}
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
                if require_hybrid and not any(item.get("stage") == "candidate_retrieval" and item.get("adapter") in {"bge_hybrid", "mixed"} and item.get("model_invoked") and (item.get("query_count") or 0) > 0 and item.get("fallback_reason") in {None, "no_dense_query"} for item in result.get("runtime_decisions", [])):
                    raise SmokeFailure("Planning did not demonstrate an actual BGE retrieval call.")
                if require_amap:
                    weather = [fact for fact in result.get("provider_facts", []) if fact.get("kind") == "weather"]
                    legs = [leg for candidate in result["plans"] for leg in candidate.get("route_legs", [])]
                    if not weather or any(fact.get("source") not in {"amap_live", "cache"} or fact.get("mode") not in {"live", "record"} or fact.get("degraded") for fact in weather) or not legs or any(leg.get("provider_mode") not in {"live", "record"} or leg.get("degraded") for leg in legs):
                        raise SmokeFailure("Planning did not demonstrate live Amap weather and routes without fallback.")
    finally:
        for session, token in created:
            with fetch(f"/api/sessions/{session}", method="DELETE", token=token):
                pass
    print(f"PASS: web + public auth + visitor isolation + examples; release={version}" + ("; SSE planning passed" if plan else "") + ("; BGE prewarm + dense retrieval passed" if require_hybrid else "") + ("; live Amap search/weather/routes passed" if require_amap else ""))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--expected-release")
    parser.add_argument("--plan", action="store_true", help="Also execute one real planning turn (uses configured site providers).")
    parser.add_argument("--require-hybrid", action="store_true", help="Require prewarm and an actual BGE call in an SSE planning turn.")
    parser.add_argument("--require-amap", action="store_true", help="Require configured map, live departure search, weather and routes (uses site quota).")
    args = parser.parse_args()
    try:
        run(args.base_url, expected_release=args.expected_release, plan=args.plan, require_hybrid=args.require_hybrid, require_amap=args.require_amap)
    except Exception as error:
        # Do not dump response bodies, credentials or provider diagnostics.
        message = str(error) if isinstance(error, SmokeFailure) else type(error).__name__
        raise SystemExit("FAIL: " + message) from None


if __name__ == "__main__":
    main()
