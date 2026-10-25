"""Single-process public demo boundary: expiring capabilities, budgets and BYOK.

Credentials stay outside graph state, runnable config and database records. Models
are selected in the executing thread, including SSE workers, never via os.environ.
"""
from __future__ import annotations

import hashlib
from http.cookies import SimpleCookie, CookieError
import secrets
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable

from starlette.responses import JSONResponse
from langsmith import tracing_context

from app.services.demo_router import DemoRouter
from app.services.planning_intent import RuleBasedPlanningIntentProvider, build_default_planning_intent_provider
from app.services.recommendation_advisor import RuleBasedRecommendationAdvisor, build_default_recommendation_advisor
from app.services.router_extractor import build_default_turn_interpreter


class DemoAccessError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


@dataclass(frozen=True, repr=False)
class ModelBundle:
    router: object
    intent: object
    advisor: object


RULE_MODELS = ModelBundle(DemoRouter(), RuleBasedPlanningIntentProvider(), RuleBasedRecommendationAdvisor())
_models: ContextVar[ModelBundle] = ContextVar("public_demo_models", default=RULE_MODELS)


class VisitorRouter:
    def interpret_with_runtime(self, *args, **kwargs):
        return _models.get().router.interpret_with_runtime(*args, **kwargs)


class VisitorIntent:
    def decide(self, request):
        return _models.get().intent.decide(request)


class VisitorAdvisor:
    def advise(self, request):
        return _models.get().advisor.advise(request)


def deepseek_models(key: str) -> ModelBundle:
    # Only the official endpoint and the existing bounded schema adapter are selectable.
    options = dict(api_key=key, model_name="deepseek-flash", base_url="https://api.deepseek.com", timeout_seconds=15.0)
    return ModelBundle(
        build_default_turn_interpreter(**options),
        build_default_planning_intent_provider("llm", **options),
        build_default_recommendation_advisor("llm", **options),
    )


@dataclass(repr=False)
class Visitor:
    user_id: str
    expires_at: float
    models: ModelBundle = field(default_factory=lambda: RULE_MODELS, repr=False)
    busy: bool = False
    turns: int = 0
    sessions: int = 0
    requests: deque = field(default_factory=deque, repr=False)


class PublicDemo:
    def __init__(self, *, ttl_seconds: int = 7200, max_visitors: int = 100,
                 max_turns: int = 30, max_concurrent: int = 2,
                 clock: Callable[[], float] = time.monotonic,
                 model_factory: Callable[[str], ModelBundle] = deepseek_models):
        self.ttl_seconds = ttl_seconds
        self.max_visitors = max_visitors
        self.max_turns = max_turns
        self.max_concurrent = max_concurrent
        self.clock = clock
        self.model_factory = model_factory
        self._lock = threading.RLock()
        self._visitors: dict[str, Visitor] = {}
        self._ips: dict[str, deque] = {}
        self._running = 0
        self._provider_requests: deque = deque()

    def issue(self, ip: str) -> dict:
        with self._lock:
            now = self.clock()
            # Do not trust forwarded headers: proxy must restrict backend ingress.
            self._ips = {host: times for host, times in self._ips.items() if times and times[-1] > now - 60}
            if ip not in self._ips and len(self._ips) >= 1000:
                raise DemoAccessError(429, "访问人数较多，请稍后再试。")
            times = self._ips.setdefault(ip, deque())
            while times and times[0] <= now - 60:
                times.popleft()
            if len(times) >= 10 or len(self._visitors) >= self.max_visitors:
                raise DemoAccessError(429, "临时体验名额已满或刷新过于频繁，请稍后再试。")
            times.append(now)
            token = secrets.token_urlsafe(32)
            visitor = Visitor("guest-" + secrets.token_hex(16), now + self.ttl_seconds)
            self._visitors[self._digest(token)] = visitor
            return {"token": token, "expires_in_seconds": self.ttl_seconds, "mode": "demo", "model": "deepseek-flash"}

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def authenticate(self, token: str) -> Visitor:
        with self._lock:
            visitor = self._visitors.get(self._digest(token))
            if not visitor or visitor.expires_at <= self.clock():
                raise DemoAccessError(401, "本次临时体验已过期，请刷新页面重新开始。")
            return visitor

    def consume_request(self, visitor: Visitor, path: str, method: str):
        with self._lock:
            if visitor.busy and method not in ("GET", "HEAD", "OPTIONS"):
                raise DemoAccessError(409, "请等待当前规划完成后再操作。")
            now = self.clock()
            while visitor.requests and visitor.requests[0] <= now - 60:
                visitor.requests.popleft()
            if len(visitor.requests) >= 90:
                raise DemoAccessError(429, "操作过于频繁，请稍后再试。")
            visitor.requests.append(now)
            if path == "/api/departure-locations/search" or path.startswith("/_AMapService/"):
                while self._provider_requests and self._provider_requests[0] <= now - 60:
                    self._provider_requests.popleft()
                if len(self._provider_requests) >= 120:
                    raise DemoAccessError(429, "地图服务本分钟体验额度已用完，请稍后再试。")
                self._provider_requests.append(now)
            if method == "POST" and path == "/api/sessions":
                if visitor.sessions >= 12:
                    raise DemoAccessError(429, "本次体验最多创建 12 个规划会话。")
                visitor.sessions += 1

    def configure(self, visitor: Visitor, key: str | None):
        with self._lock:
            if visitor.busy:
                raise DemoAccessError(409, "请等待本轮规划完成后再更换模型配置。")
            if visitor.expires_at <= self.clock():
                raise DemoAccessError(401, "本次临时体验已过期，请刷新页面。")
            visitor.models = self.model_factory(key) if key else RULE_MODELS

    @contextmanager
    def turn(self, user_id: str):
        with self._lock:
            visitor = next((v for v in self._visitors.values() if v.user_id == user_id), None)
            if not visitor or visitor.expires_at <= self.clock():
                raise DemoAccessError(401, "本次临时体验已过期，请刷新页面。")
            if visitor.busy or self._running >= self.max_concurrent:
                raise DemoAccessError(429, "已有规划正在运行，请稍后再试。")
            if visitor.turns >= self.max_turns:
                raise DemoAccessError(429, "本次体验的规划次数已用完。")
            visitor.busy = True
            visitor.turns += 1
            self._running += 1
            context_token = _models.set(visitor.models)
        try:
            # Public visitor prompts/configuration must not enter server-side
            # hosted LangSmith traces inherited from a developer environment.
            with tracing_context(enabled=False):
                yield
        finally:
            _models.reset(context_token)
            with self._lock:
                visitor.busy = False
                self._running -= 1

    def expired_users(self) -> list[str]:
        with self._lock:
            expired = [(token, v.user_id) for token, v in self._visitors.items() if v.expires_at <= self.clock() and not v.busy]
            for token, _ in expired:
                del self._visitors[token]  # Releases references to BYOK clients.
            return [user for _, user in expired]

    def clear(self):
        with self._lock:
            self._visitors.clear()
            self._ips.clear()
            self._provider_requests.clear()


class PublicDemoGate:
    """ASGI auth boundary also covers streamed responses and external providers."""
    def __init__(self, app, demo: PublicDemo):
        self.app, self.demo = app, demo

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        headers = dict(scope.get("headers", []))
        try:
            if path not in ("/api/health", "/api/config/runtime", "/api/demo/visitors") and method != "OPTIONS":
                token = headers.get(b"x-demo-token", b"").decode("ascii", errors="ignore")
                if not token and path.startswith("/_AMapService/"):
                    cookies = SimpleCookie()
                    try:
                        cookies.load(headers.get(b"cookie", b"").decode("latin-1"))
                        token = cookies["hft_map_access"].value if "hft_map_access" in cookies else ""
                    except CookieError:
                        token = ""
                visitor = self.demo.authenticate(token)
                self.demo.consume_request(visitor, path, method)
                scope = dict(scope)
                scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k.lower() != b"x-user-id"] + [(b"x-user-id", visitor.user_id.encode())]
                scope["state"] = {**scope.get("state", {}), "demo_visitor": visitor}
            # Bound all request bodies, including chunked requests, before JSON parsing.
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                size += len(message.get("body", b""))
                if size > 16384:
                    raise DemoAccessError(413, "提交内容过长，请缩短输入。")
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    break
            delivered = False
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()
            async def private_send(message):
                if message["type"] == "http.response.start":
                    message = dict(message)
                    message["headers"] = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"cache-control"] + [(b"cache-control", b"no-store"), (b"referrer-policy", b"no-referrer")]
                await send(message)
            await self.app(scope, bounded_receive, private_send)
        except DemoAccessError as error:
            await JSONResponse({"detail": error.message}, status_code=error.status, headers={"Cache-Control": "no-store", **({"Retry-After": "60"} if error.status == 429 else {})})(scope, receive, send)
