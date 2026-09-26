"""Public planning behaviour plus the narrow internal repair scheduling seam."""

import unittest
from collections import deque
from datetime import datetime, timezone

from app.domain.catalog import ResourceType
from app.domain.constraints import ConstraintSource, ConstraintValue
from app.domain.planning import Plan, PlanStrategy, Stop, StopRole, StopType
from app.domain.providers import GeoPoint, ProviderMode, ProviderSource, RouteFact, RouteMode
from app.providers.availability import MockAvailabilityProvider
from app.domain.providers import AvailabilityStatus
from app.services.catalog import InMemoryCatalog
from app.services.planning import (
    RepairOutcome,
    PlanningService,
    _FinalistAttempt,
    _RepairCoordinator,
    _RepairContext,
)
from app.services.plan_verifier import VerificationFinding
from tests.test_native_planning import SOURCE, candidate
from tests.test_planning import planning_constraints


class DestinationRouteProvider:
    def __init__(self) -> None:
        self.calls = 0

    def route(self, request):
        self.calls += 1
        long = request.destination.longitude > 116.5
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=RouteMode.TAXI,
            distance_km=50 if long else 1,
            duration_minutes=20 if long else 5,
            geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY,
            provider_mode=ProviderMode.REPLAY,
            verified_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )


class ReturnDeadlineRouteProvider:
    """Only the far restaurant makes the final return leg miss its deadline."""

    def route(self, request):
        is_return = round(request.destination.longitude, 4) == 116.4436
        is_far_return = is_return and request.origin.longitude > 116.5
        duration = 60 if is_far_return else 1
        return RouteFact(
            origin=request.origin,
            destination=request.destination,
            mode=RouteMode.TAXI,
            distance_km=4 if is_far_return else 1,
            duration_minutes=duration,
            geometry=[request.origin, request.destination],
            source=ProviderSource.REPLAY,
            provider_mode=ProviderMode.REPLAY,
            verified_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        )


class CountingAvailabilityProvider(MockAvailabilityProvider):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls = 0

    def check(self, request):
        self.calls += 1
        return super().check(request)


def _plan(fingerprint: str, activity: str, meal: str) -> Plan:
    def stop(resource_id: str, role: StopRole, name: str) -> Stop:
        return Stop(
            resource_id=resource_id,
            type=StopType.ACTIVITY if role == StopRole.ACTIVITY else StopType.RESTAURANT,
            role=role,
            name=name,
            start="14:00",
            end="15:00",
            duration_minutes=60,
            source=SOURCE,
        )
    return Plan(
        plan_id=f"plan-{fingerprint}", composition_fingerprint=fingerprint,
        skeleton_id="activity-meal-v1", title=fingerprint, strategy=PlanStrategy.BALANCED,
        total_score=1, total_price=0, total_duration_minutes=120,
        stops=[stop(activity, StopRole.ACTIVITY, activity), stop(meal, StopRole.MEAL, meal)],
        route_legs=[],
    )


class RepairContractTest(unittest.TestCase):
    @staticmethod
    def _context(
        failed: _FinalistAttempt,
        findings: tuple[VerificationFinding, ...],
    ) -> _RepairContext:
        return _RepairContext(
            failed=failed,
            findings=findings,
            remaining_route_leg_budget=20,
            remaining_availability_batches=4,
        )

    def test_opening_hours_failure_replaces_only_the_attributable_stop(self) -> None:
        catalog = InMemoryCatalog([
            candidate("activity", ResourceType.ACTIVITY, "展览", ["展览"]),
            candidate("meal-closed", ResourceType.RESTAURANT, "已关门餐厅", ["餐厅"], open_hours={"sat": "09:00-10:00"}),
            candidate("meal-open", ResourceType.RESTAURANT, "营业餐厅", ["餐厅"], open_hours={"sat": "14:00-20:00"}),
        ])
        result = PlanningService(
            catalog=catalog,
            route_provider=DestinationRouteProvider(),
            availability_provider=MockAvailabilityProvider(statuses={"activity": AvailabilityStatus.AVAILABLE, "meal-open": AvailabilityStatus.AVAILABLE, "meal-closed": AvailabilityStatus.AVAILABLE}),
        ).plan(planning_constraints(budget=1_000, time_end="18:00"))

        self.assertTrue(result.plans)
        self.assertTrue(all("meal-closed" not in {stop.resource_id for stop in plan.stops} for plan in result.plans))
        self.assertTrue(any([stop.resource_id for stop in plan.stops] == ["activity", "meal-open"] for plan in result.plans))

    def test_overlong_route_leg_replaces_the_leg_destination_and_reverifies(self) -> None:
        far_meal = candidate("meal-far", ResourceType.RESTAURANT, "远餐厅", ["餐厅"]).model_copy(
            update={"location": GeoPoint(latitude=39.92, longitude=116.7)}
        )
        near_meal = candidate("meal-near", ResourceType.RESTAURANT, "近餐厅", ["餐厅"]).model_copy(
            update={"location": GeoPoint(latitude=39.92, longitude=116.45)}
        )
        catalog = InMemoryCatalog([
            candidate("activity", ResourceType.ACTIVITY, "展览", ["展览"]), far_meal, near_meal,
        ])
        provider = DestinationRouteProvider()
        availability = CountingAvailabilityProvider(
            statuses={
                "activity": AvailabilityStatus.AVAILABLE,
                "meal-far": AvailabilityStatus.AVAILABLE,
                "meal-near": AvailabilityStatus.AVAILABLE,
            }
        )
        result = PlanningService(
            catalog=catalog,
            route_provider=provider,
            availability_provider=availability,
        ).plan(planning_constraints(budget=1_000, time_end="18:00", max_distance_km=40))

        self.assertTrue(result.plans)
        self.assertTrue(all("meal-far" not in {stop.resource_id for stop in plan.stops} for plan in result.plans))
        self.assertTrue(all(max(leg.distance_km for leg in plan.route_legs) <= 40 for plan in result.plans))
        self.assertGreater(provider.calls, 2)
        self.assertLessEqual(provider.calls, 24)
        self.assertLessEqual(availability.calls, 6)

    def test_late_return_replaces_the_attributable_last_stop_and_reverifies(self) -> None:
        far_meal = candidate("meal-far", ResourceType.RESTAURANT, "远餐厅", ["餐厅"]).model_copy(
            update={"location": GeoPoint(latitude=39.92, longitude=116.7)}
        )
        near_meal = candidate("meal-near", ResourceType.RESTAURANT, "近餐厅", ["餐厅"]).model_copy(
            update={"location": GeoPoint(latitude=39.92, longitude=116.45)}
        )
        constraints = planning_constraints(
            budget=1_000, time_end="17:00", max_distance_km=40
        ).model_copy(
            update={
                "return_by": ConstraintValue(
                    value="17:00", source=ConstraintSource.USER_EXPLICIT
                )
            }
        )
        result = PlanningService(
            catalog=InMemoryCatalog([
                candidate("activity", ResourceType.ACTIVITY, "展览", ["展览"]),
                far_meal,
                near_meal,
            ]),
            route_provider=ReturnDeadlineRouteProvider(),
            availability_provider=MockAvailabilityProvider(
                statuses={
                    "activity": AvailabilityStatus.AVAILABLE,
                    "meal-far": AvailabilityStatus.AVAILABLE,
                    "meal-near": AvailabilityStatus.AVAILABLE,
                }
            ),
        ).plan(constraints)

        self.assertTrue(result.plans)
        self.assertTrue(
            all("meal-far" not in {stop.resource_id for stop in plan.stops} for plan in result.plans)
        )
        self.assertTrue(all(plan.route_legs[-1].end <= "17:00" for plan in result.plans))

    def test_two_round_chain_exhaustion_leaves_independent_finalist_queued(self) -> None:
        root = _FinalistAttempt(_plan("root", "activity-a", "meal-a"), "root")
        first = _FinalistAttempt(_plan("first", "activity-a", "meal-b"), "first")
        second = _FinalistAttempt(_plan("second", "activity-a", "meal-c"), "second")
        independent = _FinalistAttempt(_plan("independent", "activity-b", "meal-a"), "independent")
        pending = deque([first, second, independent])
        coordinator = _RepairCoordinator()
        finding = (VerificationFinding("availability_verified_unavailable", "availability", "unavailable", resource_id="meal-a"),)

        first_decision = coordinator.decide(
            context=self._context(root, finding),
            pending=pending,
            verified_compositions={"root"},
        )
        self.assertEqual(first_decision.outcome, RepairOutcome.LOCAL_REPLACEMENT)
        second_decision = coordinator.decide(
            context=self._context(
                first_decision.replacement,
                (VerificationFinding("availability_verified_unavailable", "availability", "unavailable", resource_id="meal-b"),),
            ),
            pending=pending,
            verified_compositions={"root", "first"},
        )
        self.assertEqual(second_decision.outcome, RepairOutcome.LOCAL_REPLACEMENT)
        terminal = coordinator.decide(
            context=self._context(
                second_decision.replacement,
                (VerificationFinding("availability_verified_unavailable", "availability", "unavailable", resource_id="meal-c"),),
            ),
            pending=pending,
            verified_compositions={"root", "first", "second"},
        )

        self.assertEqual(terminal.outcome, RepairOutcome.TERMINAL)
        self.assertEqual(pending[0].plan.composition_fingerprint, "independent")


if __name__ == "__main__":
    unittest.main()
