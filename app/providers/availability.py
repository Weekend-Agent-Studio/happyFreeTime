"""Bounded dynamic availability adapters for finalist visits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Callable, Protocol

from app.domain.providers import (
    AvailabilityFact,
    AvailabilityRequest,
    AvailabilityStatus,
    ProviderMode,
    ProviderSource,
)


class AvailabilityProvider(Protocol):
    def check(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        ...


class AvailabilityReplayStore(Protocol):
    def save(self, request: AvailabilityRequest, facts: list[AvailabilityFact]) -> None:
        ...

    def load(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        ...


class MockAvailabilityProvider:
    """Offline fixture adapter. Unspecified resources remain explicitly unknown."""

    def __init__(
        self,
        *,
        statuses: dict[str, AvailabilityStatus] | None = None,
        default_status: AvailabilityStatus = AvailabilityStatus.UNKNOWN,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._statuses = statuses or {}
        self._default_status = default_status
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        now = self._clock()
        facts: list[AvailabilityFact] = []
        for check in request.checks:
            status = self._statuses.get(check.resource_id, self._default_status)
            facts.append(
                AvailabilityFact(
                    resource_id=check.resource_id,
                    status=status,
                    source=ProviderSource.MOCK,
                    mode=ProviderMode.MOCK,
                    observed_at=now,
                    verified_at=now,
                    verified=status != AvailabilityStatus.UNKNOWN,
                    reason=("mock_verified_unavailable" if status == AvailabilityStatus.UNAVAILABLE else "mock_no_availability_evidence" if status == AvailabilityStatus.UNKNOWN else None),
                )
            )
        return facts


class DemoAvailabilityProvider(MockAvailabilityProvider):
    """Deterministic Demo World availability, still consumed by the verifier.

    This is intentionally not called live stock.  A small stable subset is
    unavailable so repair paths can be demonstrated without turning every
    ordinary request into a warning-only unknown state.
    """

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        super().__init__(clock=clock)

    def check(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        now = self._clock()
        facts: list[AvailabilityFact] = []
        for check in request.checks:
            value = int.from_bytes(hashlib.sha256(check.resource_id.encode()).digest()[:2], "big")
            status = AvailabilityStatus.UNAVAILABLE if value % 47 == 0 else AvailabilityStatus.AVAILABLE
            facts.append(
                AvailabilityFact(
                    resource_id=check.resource_id,
                    status=status,
                    source=ProviderSource.MOCK,
                    mode=ProviderMode.MOCK,
                    observed_at=now,
                    verified_at=now,
                    verified=True,
                    reason=("demo_world_simulated_unavailable" if status == AvailabilityStatus.UNAVAILABLE else "demo_world_simulated_available"),
                )
            )
        return facts


class InMemoryAvailabilityReplayStore:
    def __init__(self) -> None:
        self._facts: dict[str, list[AvailabilityFact]] = {}

    def save(self, request: AvailabilityRequest, facts: list[AvailabilityFact]) -> None:
        self._facts[request.cache_key] = [fact.model_copy(deep=True) for fact in facts]

    def load(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        try:
            return [fact.model_copy(deep=True) for fact in self._facts[request.cache_key]]
        except KeyError as error:
            raise LookupError(f"availability replay not found: {request.cache_key}") from error


class RecordingAvailabilityProvider:
    def __init__(self, *, delegate: AvailabilityProvider, store: AvailabilityReplayStore) -> None:
        self._delegate, self._store = delegate, store

    def check(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        facts = [fact.model_copy(update={"mode": ProviderMode.RECORD}) for fact in self._delegate.check(request)]
        self._store.save(request, facts)
        return facts


class ReplayAvailabilityProvider:
    def __init__(self, *, store: AvailabilityReplayStore, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        now = self._clock()
        return [fact.model_copy(update={"source": ProviderSource.REPLAY, "mode": ProviderMode.REPLAY, "verified_at": now, "degraded": False, "degraded_reason": None}) for fact in self._store.load(request)]


@dataclass
class _CacheEntry:
    facts: list[AvailabilityFact]
    stored_at: datetime


class InMemoryAvailabilityCache:
    def __init__(self) -> None:
        self.successes: dict[str, _CacheEntry] = {}
        self.failures: dict[str, datetime] = {}


class ResilientAvailabilityProvider:
    def __init__(self, *, primary: AvailabilityProvider, fallback: AvailabilityProvider, cache: InMemoryAvailabilityCache, success_ttl: timedelta, failure_ttl: timedelta, clock: Callable[[], datetime] | None = None) -> None:
        self._primary, self._fallback, self._cache = primary, fallback, cache
        self._success_ttl, self._failure_ttl = success_ttl, failure_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def check(self, request: AvailabilityRequest) -> list[AvailabilityFact]:
        now, key = self._clock(), request.cache_key
        cached = self._cache.successes.get(key)
        if cached and now - cached.stored_at <= self._success_ttl:
            return [fact.model_copy(update={"source": ProviderSource.CACHE, "verified_at": now}) for fact in cached.facts]
        failed_at = self._cache.failures.get(key)
        if failed_at and now - failed_at <= self._failure_ttl:
            return self._fallback_facts(request, now, "failure_cache_active")
        try:
            facts = self._primary.check(request)
        except Exception:
            self._cache.failures[key] = now
            if cached:
                return [fact.model_copy(update={"source": ProviderSource.CACHE, "verified_at": now, "stale": True, "degraded": True, "degraded_reason": "upstream_failed_using_stale_cache"}) for fact in cached.facts]
            return self._fallback_facts(request, now, "upstream_failed_using_fallback")
        self._cache.successes[key] = _CacheEntry(facts=[fact.model_copy(deep=True) for fact in facts], stored_at=now)
        self._cache.failures.pop(key, None)
        return facts

    def _fallback_facts(self, request: AvailabilityRequest, now: datetime, reason: str) -> list[AvailabilityFact]:
        return [fact.model_copy(update={"verified_at": now, "degraded": True, "degraded_reason": reason}) for fact in self._fallback.check(request)]
