import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.domain.constraints import (
    ActorContext,
    ConstraintSource,
    IdentityType,
    Intent,
    Interpretation,
    PlanRequest,
    RawConstraints,
)
from app.domain.constraints import GeoLocation
from app.services.constraint_engine import ConstraintEngine, NeedsClarification
from app.services.enrichment import EnrichmentService, EnvironmentContext
from app.providers.geocoding import MockGeocodingProvider


class EnrichmentServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext(
            user_id="demo",
            session_id="enrichment-contract",
            identity_type=IdentityType.DEMO,
        )
        self.environment = EnvironmentContext(
            now=datetime(2026, 10, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )
        self.service = EnrichmentService()

    @staticmethod
    def interpretation(raw: RawConstraints, **kwargs: object) -> Interpretation:
        return Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=raw,
            **kwargs,
        )

    def test_enrichment_only_emits_non_temporal_patch_fields(self) -> None:
        result = self.service.enrich(
            self.interpretation(
                RawConstraints(
                    budget_per_person=180,
                    preferences=["轻松"],
                    date_text="明天",
                )
            ),
            self.actor,
            self.environment,
        )

        self.assertNotIn("planning_window", result.request_patch.set_fields)
        self.assertNotIn("date", result.request_patch.set_fields)
        self.assertEqual(result.request_patch.set_fields["preferences"], ["轻松"])
        self.assertEqual(
            result.request_patch.set_fields["budget_per_person"].value, 180
        )

    def test_non_temporal_defaults_are_visible_and_do_not_guess_companions(self) -> None:
        result = self.service.enrich(
            self.interpretation(RawConstraints()), self.actor, self.environment
        )
        fields = result.request_patch.set_fields

        self.assertEqual(fields["budget_per_person"].value, 120)
        self.assertEqual(fields["max_distance_km"].value, 8.0)
        self.assertEqual(fields["party"].value.adults, 1)
        self.assertEqual(fields["party"].value.children, 0)
        self.assertTrue(any(item.field == "budget_per_person" for item in result.assumptions))
        self.assertTrue(any(item.field == "party" for item in result.assumptions))

    def test_relationship_member_does_not_infer_adult_count(self) -> None:
        result = self.service.enrich(
            self.interpretation(
                RawConstraints(members=["女朋友"]),
                evidence_map={"party": "女朋友"},
            ),
            self.actor,
            self.environment,
        )

        party = result.request_patch.set_fields["party"].value
        self.assertEqual(party.members, ["女朋友"])
        self.assertEqual(party.adults, 1)
        self.assertEqual(party.children, 0)

    def test_strict_budget_without_amount_becomes_engine_issue(self) -> None:
        result = self.service.enrich(
            self.interpretation(RawConstraints(strict_budget=True)),
            self.actor,
            self.environment,
        )
        outcome = ConstraintEngine().apply(
            PlanRequest(),
            result.request_patch,
        )

        self.assertIsInstance(outcome, NeedsClarification)
        self.assertEqual(outcome.issue.field, "budget_per_person")

    def test_unresolved_explicit_location_is_not_replaced_by_default(self) -> None:
        service = EnrichmentService(
            geocoding_provider=MockGeocodingProvider.from_locations({})
        )
        result = service.enrich(
            self.interpretation(RawConstraints(location_text="我公司附近")),
            self.actor,
            self.environment,
        )

        self.assertNotIn("location", result.request_patch.set_fields)


if __name__ == "__main__":
    unittest.main()
