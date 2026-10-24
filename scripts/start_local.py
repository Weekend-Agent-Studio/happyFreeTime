"""Start the local backend and Vite together with explicit, isolated presets."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


def environments(mode: str, api_port: int, full_llm: bool = False):
    if full_llm and mode != "Development":
        raise ValueError("-FullLlm is for Development; PublicDemo accepts a Key in the page.")
    backend, frontend = os.environ.copy(), os.environ.copy()
    public = mode == "PublicDemo"
    backend.update({
        "HFT_PUBLIC_DEMO": str(public).lower(),
        "HFT_DATABASE_PATH": str(ROOT / "data" / ("local_public_demo.db" if public else "happy_free_time_v2.db")),
        "HFT_DEMO_MODE": "0" if full_llm else "1",
        "HFT_PLANNING_INTENT_MODE": "llm" if full_llm else "rule",
        "HFT_RECOMMENDATION_ADVISOR_MODE": "llm" if full_llm else "rule",
        "HFT_CANDIDATE_RETRIEVER_MODE": "hybrid" if full_llm else "rule",
    })
    if public:
        frontend["VITE_SHOW_DEVELOPER_DETAILS"] = "false"
        backend.update({
            "LLM_API": "", "HFT_CATALOG_MODE": "demo", "HFT_PROVIDER_MODE": "mock",
            "HFT_ROUTE_PROVIDER_MODE": "mock", "HFT_GEOCODING_PROVIDER_MODE": "mock",
            "AMAP_WEB_SERVICE_KEY": "", "AMAP_JS_API_KEY": "", "AMAP_JS_SECURITY_CODE": "",
            "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
        })
    frontend.update({"VITE_PUBLIC_DEMO": str(public).lower(), "HFT_API_PROXY_TARGET": f"http://127.0.0.1:{api_port}"})
    return backend, frontend


def ensure_free_ports(ports):
    if len(set(ports)) != len(ports):
        raise ValueError("Backend and frontend ports must differ.")
    for port in ports:
        if not 1 <= port <= 65535:
            raise ValueError("Ports must be between 1 and 65535.")
        with socket.socket() as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise ValueError(f"Port {port} is occupied or reserved; stop its service or choose another port.") from None


def wait_ready(children, api_port, frontend_port, public, timeout=60):
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for name, child in children:
            if child.poll() is not None:
                raise RuntimeError(f"{name} exited during startup (code {child.returncode}). Check its log file.")
        try:
            with opener.open(f"http://127.0.0.1:{api_port}/api/config/runtime", timeout=1) as response:
                config = json.load(response)["data"]
            if config.get("public_demo") is not public:
                raise RuntimeError("Backend mode differs from the requested preset.")
            with opener.open(f"http://127.0.0.1:{frontend_port}/", timeout=1) as response:
                if "text/html" not in response.headers.get("Content-Type", ""):
                    raise RuntimeError("Frontend did not return HTML.")
            return
        except (OSError, ValueError, KeyError):
            time.sleep(.2)
    raise RuntimeError("Local services did not become ready within 60 seconds. Check the log files.")


def stop(children):
    # Only processes started by this invocation. Never kill a service by port.
    for _, child in reversed(children):
        if child.poll() is None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass
    for _, child in reversed(children):
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("PublicDemo", "Development"), default="PublicDemo")
    parser.add_argument("--api-port", type=int, default=0)
    parser.add_argument("--frontend-port", type=int, default=0)
    parser.add_argument("--full-llm", action="store_true")
    parser.add_argument("--check", action="store_true", help="Start, verify and stop both services; no browser or LLM calls.")
    args = parser.parse_args()
    public = args.mode == "PublicDemo"
    api_port = args.api_port or (8001 if public else 8000)
    frontend_port = args.frontend_port or (5174 if public else 5173)
    children = []
    log_files = []
    try:
        backend_env, frontend_env = environments(args.mode, api_port, args.full_llm)
        node = shutil.which("node")
        vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
        if not node or not vite.is_file():
            raise RuntimeError("Node.js and installed frontend dependencies are required. Run pnpm --dir frontend install first.")
        ensure_free_ports((api_port, frontend_port))
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        log_dir = ROOT / ".tmp" / "local-run" / f"{args.mode}-{uuid.uuid4().hex[:8]}"
        log_dir.mkdir(parents=True, exist_ok=False)
        print(f"HappyFreeTime | {args.mode}\nDatabase: {backend_env['HFT_DATABASE_PATH']}\nLogs: {log_dir}", flush=True)
        if args.full_llm:
            print("Real LLM preset enabled; uses .env credentials. Site provider settings are unchanged.", flush=True)
        services = [
            ("Backend", [sys.executable, "-m", "uvicorn", "app.api.server:app", "--host", "127.0.0.1", "--port", str(api_port), "--workers", "1", "--no-access-log"], ROOT, backend_env),
            ("Frontend", [node, str(vite), "--host", "127.0.0.1", "--port", str(frontend_port), "--strictPort"], ROOT / "frontend", frontend_env),
        ]
        for name, command, cwd, env in services:
            log_file = (log_dir / f"{name.lower()}.log").open("w", encoding="utf-8")
            log_files.append(log_file)
            children.append((name, subprocess.Popen(command, cwd=cwd, env=env, creationflags=creation_flags, stdout=log_file, stderr=subprocess.STDOUT)))
        wait_ready(children, api_port, frontend_port, public)
        print(f"\nReady: http://127.0.0.1:{frontend_port}/\nCtrl+C stops both services. Frontend edits update automatically; restart for backend changes.", flush=True)
        if not args.check:
            while True:
                for name, child in children:
                    if child.poll() is not None:
                        raise RuntimeError(f"{name} stopped (code {child.returncode}); stopping the other service.")
                time.sleep(.5)
    except KeyboardInterrupt:
        print("\nStopping local services...", flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        stop(children)
        for log_file in log_files:
            log_file.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
