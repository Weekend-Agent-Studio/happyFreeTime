import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.domain.constraints import (
    ActorContext,
    ConstraintSource,
    GeoLocation,
    IdentityType,
    Intent,
    Interpretation,
    RawConstraints,
)
from app.services.demo_router import DemoRouter
from app.services.enrichment import EnvironmentContext, EnrichmentService
from app.services.router_extractor import RouterContext


class EnrichmentServiceTest(unittest.TestCase):
    def test_date_party_is_inferred_from_the_user_phrase_not_marked_explicit(self) -> None:
        interpretation = DemoRouter().interpret(
            "安排一个轻松的约会，想吃甜品",
            RouterContext(current_date=date(2026, 8, 13)),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-date-party",
            identity_type=IdentityType.DEMO,
        )
        environment = EnvironmentContext(
            now=datetime(2026, 8, 13, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.party.value.adults, 2)
        self.assertEqual(
            result.constraints.party.source,
            ConstraintSource.USER_INFERRED,
        )
        self.assertEqual(result.constraints.party.raw_text, "约会")
        self.assertEqual(result.constraints.party.confidence, 0.85)
        self.assertNotIn("party", {item.field for item in result.assumptions})

    def test_party_without_an_extracted_count_remains_a_default_assumption(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            evidence_map={"party": "约会"},
            extraction_confidence={"party": 0.85},
            inferred_fields={"party"},
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-missing-party-count",
            identity_type=IdentityType.DEMO,
        )
        environment = EnvironmentContext(
            now=datetime(2026, 8, 13, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.party.value.adults, 1)
        self.assertEqual(result.constraints.party.source, ConstraintSource.DEFAULT_RULE)
        self.assertIn("party", {item.field for item in result.assumptions})

    def test_normalizes_weekday_expressions_without_an_llm_or_tool(self) -> None:
        current_date = date(2026, 8, 12)

        self.assertEqual(
            EnrichmentService._normalize_date("周六", current_date),
            date(2026, 8, 15),
        )
        self.assertEqual(
            EnrichmentService._normalize_date("下周六", current_date),
            date(2026, 8, 22),
        )
        self.assertEqual(
            EnrichmentService._normalize_date("星期天", current_date),
            date(2026, 8, 16),
        )

    def test_normalizes_fuzzy_language_and_exposes_defaults(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.98},
            raw_constraints=RawConstraints(
                date_text="今天",
                time_text="下午",
                max_distance_text="别太远",
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-1",
            identity_type=IdentityType.DEMO,
        )
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.date.value, date(2026, 8, 12))
        self.assertEqual(result.constraints.time_window.value.start, "14:00")
        self.assertEqual(result.constraints.time_window.value.end, "18:00")
        self.assertEqual(result.constraints.max_distance_km.value, 8.0)
        self.assertEqual(
            result.constraints.max_distance_km.source,
            ConstraintSource.USER_INFERRED,
        )
        self.assertEqual(result.constraints.budget_per_person.value, 120)
        self.assertEqual(
            result.constraints.budget_per_person.source,
            ConstraintSource.DEFAULT_RULE,
        )
        self.assertEqual(result.constraints.party.value.adults, 1)
        self.assertEqual(result.constraints.location.value.city, "北京市")
        self.assertEqual(
            {assumption.field for assumption in result.assumptions},
            {"budget_per_person", "party"},
        )

    def test_missing_planning_fields_use_visible_weekend_defaults(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-2",
            identity_type=IdentityType.DEMO,
        )
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.date.value, date(2026, 8, 15))
        self.assertEqual(result.constraints.date.source, ConstraintSource.DEFAULT_RULE)
        self.assertEqual(result.constraints.time_window.value.start, "14:00")
        self.assertEqual(result.constraints.max_distance_km.value, 8.0)
        self.assertEqual(
            {assumption.field for assumption in result.assumptions},
            {
                "date",
                "time_window",
                "budget_per_person",
                "party",
                "max_distance_km",
            },
        )

    def test_unrecognized_explicit_date_is_not_replaced_by_a_default(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(date_text="等我忙完那天"),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-3",
            identity_type=IdentityType.DEMO,
        )
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertIsNone(result.constraints.date)
        self.assertNotIn("date", {item.field for item in result.assumptions})

    def test_explicit_location_is_not_replaced_when_geocoding_is_unresolved(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(location_text="海淀黄庄附近"),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-4",
            identity_type=IdentityType.DEMO,
        )
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
        )

        unresolved = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertIsNone(unresolved.constraints.location)

        resolved_location = GeoLocation(
            city="北京市",
            district="海淀区",
            address="北京市海淀区黄庄",
            latitude=39.9756,
            longitude=116.3176,
        )
        resolved = EnrichmentService().enrich(
            interpretation,
            actor,
            environment.model_copy(update={"resolved_location": resolved_location}),
        )

        self.assertEqual(resolved.constraints.location.value, resolved_location)
        self.assertEqual(resolved.constraints.location.source, ConstraintSource.REAL_TOOL)


if __name__ == "__main__":
    unittest.main()
