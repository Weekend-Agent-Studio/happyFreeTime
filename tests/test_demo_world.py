import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.application import create_app
from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    NormalizedConstraints,
    PartyProfile,
    TimeWindow,
)
from app.domain.planning import Plan, PlanStrategy, RouteLeg, RouteSource, Stop, StopRole, StopType
from app.domain.providers import AvailabilityCheck, AvailabilityRequest, GeoPoint, ProviderMode, ProviderSource, RouteFact, RouteMode, RouteRequest
from app.providers.availability import DemoAvailabilityProvider
from app.services.demo_router import DemoRouter
from app.services.demo_world import DemoCatalog, DemoWorld
from app.services.enrichment import EnvironmentContext
from app.services.plan_verifier import PlanVerifier
from scripts.build_demo_world import ANCHORS, build


def constraints(*, budget: int = 1_000, time_window: TimeWindow | None = None) -> NormalizedConstraints:
    return NormalizedConstraints(
        date=ConstraintValue(value=date(2026, 8, 15), source=ConstraintSource.USER_EXPLICIT),
        time_window=ConstraintValue(value=time_window or TimeWindow(start="14:00", end="18:00"), source=ConstraintSource.USER_EXPLICIT),
        location=ConstraintValue(value=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436), source=ConstraintSource.SYSTEM_CONTEXT),
        party=ConstraintValue(value=PartyProfile(adults=2), source=ConstraintSource.USER_EXPLICIT),
        budget_per_person=ConstraintValue(value=budget, source=ConstraintSource.USER_EXPLICIT),
        strict_budget=True,
    )


class FixedRouteProvider:
    def route(self, request: RouteRequest) -> RouteFact:
        return RouteFact(
            origin=request.origin, destination=request.destination, mode=request.mode,
            distance_km=1.2, duration_minutes=8, geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY, provider_mode=ProviderMode.REPLAY,
            verified_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        )


class DemoWorldTest(unittest.TestCase):
    def test_generated_world_is_deterministic_complete_and_keeps_anchor_identity(self) -> None:
        self.assertEqual(build(), build())
        anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))["records"]
        world = DemoWorld()
        self.assertEqual(len(world.present_many([item["resource_id"] for item in anchors])), 200)
        for anchor in anchors:
            record = world.record(anchor["resource_id"])
            self.assertEqual(record["name"], anchor["name"])
            self.assertTrue(record["gallery"])
            self.assertTrue(record["open_hours"])
            self.assertIsNotNone(record["reference_avg_price"])

    def test_catalog_preserves_anchor_id_name_and_coordinate_but_uses_demo_pruning_fields(self) -> None:
        catalog = DemoCatalog()
        all_candidates = catalog.recall(NormalizedConstraints()).candidates
        anchor = next(item for item in json.loads(ANCHORS.read_text(encoding="utf-8"))["records"] if item["resource_id"] == all_candidates[0].resource_id)
        candidate = all_candidates[0]
        self.assertEqual(candidate.resource_id, anchor["resource_id"])
        self.assertEqual(candidate.name, anchor["name"])
        self.assertNotEqual(candidate.location.longitude, float(anchor["longitude"]))  # WGS84 -> GCJ02 remains in Snapshot seam
        self.assertEqual(candidate.price_kind.value, "known")
        self.assertTrue(candidate.open_hours)
        strict = catalog.recall(constraints(budget=1))
        self.assertLess(len(strict.candidates), len(all_candidates))
        self.assertTrue(all((item.avg_price or 0) <= 1 for item in strict.candidates))
        self.assertTrue(strict.violations)

    def test_different_types_use_template_appropriate_details_and_verifier_checks_hours(self) -> None:
        catalog = DemoCatalog()
        candidates = catalog.recall(NormalizedConstraints()).candidates
        activity = next(item for item in candidates if item.resource_type.value == "activity" and not DemoWorld().present_many([item.resource_id])[0].indoor)
        restaurant = next(item for item in candidates if item.resource_type.value == "restaurant")
        activity_detail, restaurant_detail = DemoWorld().present_many([activity.resource_id, restaurant.resource_id])
        self.assertNotEqual(activity_detail.indoor, restaurant_detail.indoor)
        self.assertGreater(restaurant_detail.reference_avg_price or 0, 0)
        self.assertIn("演示", restaurant_detail.data_notice)
        self.assertIn("路线来源和降级状态见各路线段", restaurant_detail.data_notice)
        self.assertNotIn("高德", restaurant_detail.data_notice)
        plan = Plan(
            plan_id="opening-test", composition_fingerprint="opening-test", title="晚间测试", strategy=PlanStrategy.BALANCED,
            total_score=1, total_price=activity.avg_price or 0, total_duration_minutes=60,
            stops=[Stop(resource_id=activity.resource_id, type=StopType.ACTIVITY, role=StopRole.ACTIVITY, name=activity.name, start="22:00", end="23:00", duration_minutes=60, price=activity.avg_price or 0, price_kind=activity.price_kind, category_tags=activity.category_tags, source=activity.source)],
            route_legs=[],
        )
        result = PlanVerifier().verify(plan, constraints(time_window=TimeWindow(start="21:00", end="23:30")), {activity.resource_id: activity})
        self.assertTrue(any(item.code == "visit_outside_opening_hours" for item in result.violations))

    def test_demo_availability_is_deterministic_but_uses_the_existing_provider_fact_contract(self) -> None:
        request = AvailabilityRequest(date=date(2026, 8, 15), checks=[AvailabilityCheck(resource_id="osm-node-1248471844", start="14:00", end="15:00")])
        provider = DemoAvailabilityProvider(clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc))
        first, second = provider.check(request), provider.check(request)
        self.assertEqual(first, second)
        self.assertTrue(first[0].verified)
        self.assertIn(first[0].status.value, {"available", "unavailable"})

    def test_http_sqlite_restores_poi_presentation_for_every_returned_stop(self) -> None:
        catalog = DemoCatalog()
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                database_path=Path(directory) / "demo-world.db",
                router=DemoRouter(),
                environment_provider=lambda _: EnvironmentContext(
                    now=datetime(2026, 8, 15, 10, tzinfo=ZoneInfo("Asia/Shanghai")),
                    default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436, adcode="110105"),
                ),
                route_provider=FixedRouteProvider(),
                availability_provider=DemoAvailabilityProvider(clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc)),
                catalog=catalog,
                poi_presentation_provider=catalog.presentation_provider,
            )
            with TestClient(app) as client:
                session = client.post("/api/sessions").json()["data"]["session_id"]
                response = client.post(f"/api/sessions/{session}/messages", json={"request_id": "demo-world-v1", "content": "今天下午出去玩"})
                restored = client.get(f"/api/sessions/{session}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["data"]
        self.assertTrue(body["plans"])
        ids = {stop["resource_id"] for plan in body["plans"] for stop in plan["stops"]}
        self.assertTrue(ids <= {item["resource_id"] for item in body["poi_presentations"]})
        self.assertEqual(restored.json()["data"]["latest_response"]["poi_presentations"], body["poi_presentations"])


if __name__ == "__main__":
    unittest.main()
