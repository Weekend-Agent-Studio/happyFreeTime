import unittest
from datetime import date, datetime, timedelta, timezone

from app.domain.providers import (
    ProviderMode,
    ProviderSource,
    WeatherFact,
    WeatherRequest,
)
from app.providers.weather import (
    AmapWeatherProvider,
    InMemoryWeatherCache,
    InMemoryWeatherReplayStore,
    MockWeatherProvider,
    RecordingWeatherProvider,
    ReplayWeatherProvider,
    ResilientWeatherProvider,
)


NOW = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
REQUEST = WeatherRequest(
    city="北京市", district="朝阳区", adcode="110105", date=date(2026, 8, 15)
)


def weather_fact(
    *,
    condition: str = "晴",
    source: ProviderSource = ProviderSource.AMAP_LIVE,
    mode: ProviderMode = ProviderMode.LIVE,
) -> WeatherFact:
    return WeatherFact(
        city="北京市",
        district="朝阳区",
        date=date(2026, 8, 15),
        condition=condition,
        temperature_c=28,
        precipitation_mm=3.0 if "雨" in condition else 0.0,
        is_adverse="雨" in condition,
        source=source,
        mode=mode,
        observed_at=NOW,
        verified_at=NOW,
    )


class StubWeatherProvider:
    def __init__(self, outcomes: list[WeatherFact | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def get_weather(self, request: WeatherRequest) -> WeatherFact:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class MutableClock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


class WeatherProviderContractTest(unittest.TestCase):
    def test_live_adapter_uses_the_requested_forecast_date_without_exposing_the_key(self) -> None:
        urls: list[str] = []

        def fetch_json(url: str) -> dict:
            urls.append(url)
            return {
                "status": "1",
                "forecasts": [
                    {
                        "reporttime": "2026-08-15 08:00:00",
                        "casts": [
                            {
                                "date": "2026-08-15",
                                "dayweather": "中雨",
                                "nightweather": "小雨",
                                "daytemp": "23",
                            }
                        ],
                    }
                ],
            }

        provider = AmapWeatherProvider(
            api_key="backend-secret",
            fetch_json=fetch_json,
            clock=lambda: NOW,
        )

        fact = provider.get_weather(REQUEST)

        self.assertEqual(fact.condition, "中雨转小雨")
        self.assertTrue(fact.is_adverse)
        self.assertEqual(fact.source, ProviderSource.AMAP_LIVE)
        self.assertEqual(fact.mode, ProviderMode.LIVE)
        self.assertIn("extensions=all", urls[0])
        self.assertIn("city=110105", urls[0])
        self.assertNotIn("backend-secret", fact.model_dump_json())

    def test_mock_record_and_replay_preserve_the_same_weather_observation(self) -> None:
        store = InMemoryWeatherReplayStore()
        mock = MockWeatherProvider(weather_fact(condition="中雨"))
        recorded = RecordingWeatherProvider(delegate=mock, store=store).get_weather(REQUEST)
        replayed = ReplayWeatherProvider(store=store, clock=lambda: NOW).get_weather(REQUEST)

        self.assertEqual(recorded.condition, replayed.condition)
        self.assertEqual(recorded.precipitation_mm, replayed.precipitation_mm)
        self.assertEqual(recorded.observed_at, replayed.observed_at)
        self.assertEqual(recorded.mode, ProviderMode.RECORD)
        self.assertEqual(replayed.mode, ProviderMode.REPLAY)
        self.assertEqual(replayed.source, ProviderSource.REPLAY)
        self.assertFalse(replayed.degraded)

    def test_success_cache_honors_ttl_and_exposes_age(self) -> None:
        clock = MutableClock()
        primary = StubWeatherProvider([weather_fact()])
        provider = ResilientWeatherProvider(
            primary=primary,
            fallback=MockWeatherProvider(weather_fact(source=ProviderSource.MOCK, mode=ProviderMode.MOCK)),
            cache=InMemoryWeatherCache(),
            success_ttl=timedelta(minutes=30),
            failure_ttl=timedelta(seconds=30),
            clock=clock,
        )

        first = provider.get_weather(REQUEST)
        clock.now += timedelta(minutes=10)
        second = provider.get_weather(REQUEST)

        self.assertEqual(first.source, ProviderSource.AMAP_LIVE)
        self.assertEqual(second.source, ProviderSource.CACHE)
        self.assertEqual(second.cache_age_seconds, 600)
        self.assertEqual(primary.calls, 1)

        clock.now += timedelta(minutes=21)
        provider.get_weather(REQUEST)
        self.assertEqual(primary.calls, 2)

    def test_failure_is_short_cached_and_falls_back_with_public_reason(self) -> None:
        clock = MutableClock()
        primary = StubWeatherProvider([RuntimeError("upstream timeout")])
        fallback = MockWeatherProvider(
            weather_fact(condition="小雨", source=ProviderSource.MOCK, mode=ProviderMode.MOCK)
        )
        provider = ResilientWeatherProvider(
            primary=primary,
            fallback=fallback,
            cache=InMemoryWeatherCache(),
            success_ttl=timedelta(minutes=30),
            failure_ttl=timedelta(seconds=30),
            clock=clock,
        )

        first = provider.get_weather(REQUEST)
        second = provider.get_weather(REQUEST)

        self.assertTrue(first.degraded)
        self.assertEqual(first.source, ProviderSource.MOCK)
        self.assertIn("upstream_failed", first.degraded_reason)
        self.assertIn("failure_cache", second.degraded_reason)
        self.assertEqual(primary.calls, 1)

        clock.now += timedelta(seconds=31)
        provider.get_weather(REQUEST)
        self.assertEqual(primary.calls, 2)


if __name__ == "__main__":
    unittest.main()
