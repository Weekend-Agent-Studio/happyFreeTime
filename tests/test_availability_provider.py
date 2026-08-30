import unittest
from datetime import date, datetime, timedelta, timezone

from app.domain.providers import (
    AvailabilityCheck,
    AvailabilityRequest,
    AvailabilityStatus,
    ProviderMode,
    ProviderSource,
)
from app.providers.availability import (
    InMemoryAvailabilityCache,
    InMemoryAvailabilityReplayStore,
    MockAvailabilityProvider,
    RecordingAvailabilityProvider,
    ReplayAvailabilityProvider,
    ResilientAvailabilityProvider,
)


NOW = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)
REQUEST = AvailabilityRequest(
    date=date(2026, 8, 29),
    checks=(
        AvailabilityCheck(resource_id="activity-a", start="14:00", end="15:00"),
        AvailabilityCheck(resource_id="meal-b", start="15:15", end="16:15"),
    ),
)


class StubAvailabilityProvider:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = 0

    def check(self, request):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class AvailabilityProviderContractTest(unittest.TestCase):
    def test_batch_mock_record_replay_preserves_three_value_facts(self) -> None:
        provider = MockAvailabilityProvider(
            statuses={"activity-a": AvailabilityStatus.AVAILABLE, "meal-b": AvailabilityStatus.UNKNOWN},
            clock=lambda: NOW,
        )
        store = InMemoryAvailabilityReplayStore()
        recorded = RecordingAvailabilityProvider(delegate=provider, store=store).check(REQUEST)
        replayed = ReplayAvailabilityProvider(store=store, clock=lambda: NOW).check(REQUEST)

        self.assertEqual(len(recorded), 2)
        self.assertEqual([fact.status for fact in replayed], [AvailabilityStatus.AVAILABLE, AvailabilityStatus.UNKNOWN])
        self.assertTrue(all(fact.source == ProviderSource.REPLAY for fact in replayed))
        self.assertTrue(all(fact.mode == ProviderMode.REPLAY for fact in replayed))

    def test_unavailable_is_explicit_and_unknown_is_not_promoted_to_available(self) -> None:
        facts = MockAvailabilityProvider(
            statuses={"activity-a": AvailabilityStatus.UNAVAILABLE},
            clock=lambda: NOW,
        ).check(REQUEST)

        self.assertEqual(facts[0].status, AvailabilityStatus.UNAVAILABLE)
        self.assertTrue(facts[0].verified)
        self.assertEqual(facts[1].status, AvailabilityStatus.UNKNOWN)
        self.assertFalse(facts[1].verified)

    def test_resilient_fallback_is_marked_degraded_and_failure_is_bounded(self) -> None:
        fallback = MockAvailabilityProvider(clock=lambda: NOW)
        primary = StubAvailabilityProvider([RuntimeError("timeout")])
        provider = ResilientAvailabilityProvider(
            primary=primary,
            fallback=fallback,
            cache=InMemoryAvailabilityCache(),
            success_ttl=timedelta(minutes=5),
            failure_ttl=timedelta(seconds=30),
            clock=lambda: NOW,
        )

        first = provider.check(REQUEST)
        second = provider.check(REQUEST)

        self.assertTrue(all(fact.degraded for fact in first))
        self.assertTrue(all(fact.degraded_reason == "upstream_failed_using_fallback" for fact in first))
        self.assertTrue(all(fact.degraded_reason == "failure_cache_active" for fact in second))
        self.assertEqual(primary.calls, 1)


if __name__ == "__main__":
    unittest.main()
