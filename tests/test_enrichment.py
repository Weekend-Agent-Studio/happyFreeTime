import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.domain.constraints import (
    ActorContext,
    ConstraintValue,
    ConstraintSource,
    DateReference,
    GeoLocation,
    IdentityType,
    Intent,
    Interpretation,
    NormalizedConstraints,
    RawConstraints,
    StopRole,
    TimeScope,
    TimeWindow,
    Weekday,
)
from app.services.demo_router import DemoRouter
from app.services.enrichment import EnvironmentContext, EnrichmentService
from app.providers.geocoding import MockGeocodingProvider
from app.services.router_extractor import RouterContext


class EnrichmentServiceTest(unittest.TestCase):
    def test_return_by_model_rejects_non_clock_value(self) -> None:
        with self.assertRaises(ValidationError):
            NormalizedConstraints(
                return_by=ConstraintValue(
                    value="十八点",
                    source=ConstraintSource.USER_EXPLICIT,
                    raw_text="最晚十八点回家",
                ),
            )

    def test_departure_at_requires_a_canonical_clock_in_raw_and_normalized_constraints(self) -> None:
        self.assertEqual(RawConstraints(departure_at="14:30").departure_at, "14:30")
        with self.assertRaises(ValidationError):
            RawConstraints(departure_at="下午两点半")
        with self.assertRaises(ValidationError):
            NormalizedConstraints(
                departure_at=ConstraintValue(value="24:00", source=ConstraintSource.USER_EXPLICIT)
            )

    def test_normalizes_explicit_departure_clocks_without_guessing(self) -> None:
        for raw_text, expected in (
            ("下午两点半准时出发", "14:30"),
            ("下午三点二十准时出发", "15:20"),
            ("下午七点准时出发", "19:00"),
            ("下午 2:30 出发", "14:30"),
            ("14:30 出发", "14:30"),
        ):
            self.assertEqual(
                EnrichmentService._normalize_departure_at(raw_text, None),
                expected,
            )
        self.assertIsNone(EnrichmentService._normalize_departure_at("下午出发", None))
        self.assertIsNone(EnrichmentService._normalize_departure_at("两点左右出发", None))

    def test_normalizes_chinese_return_deadline(self) -> None:
        self.assertEqual(
            EnrichmentService._normalize_return_by("晚上八点前回来", None),
            "20:00",
        )
        self.assertEqual(
            EnrichmentService._normalize_return_by("最晚下午三点二十到家", None),
            "15:20",
        )

    def test_departure_and_return_construct_a_window_without_losing_departure(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                departure_at_text="下午两点半准时出发",
                return_by_text="18:00前回家",
            ),
        )
        actor = ActorContext(user_id="demo", session_id="departure", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.departure_at.value, "14:30")
        self.assertEqual(result.constraints.return_by.value, "18:00")
        self.assertEqual(result.constraints.time_window.value.model_dump(), {"start": "14:30", "end": "18:00"})

    def test_exact_departure_overrides_inferred_afternoon_scope(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                time_text="下午",
                time_scope=TimeScope.AFTERNOON,
                departure_at_text="下午六点半准时出发",
            ),
            evidence_map={"time_text": "下午", "departure_at_text": "下午六点半准时出发"},
            extraction_confidence={"time_text": 1.0, "departure_at_text": 1.0},
        )
        actor = ActorContext(user_id="demo", session_id="departure-scope", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.departure_at.value, "18:30")
        self.assertEqual(result.constraints.time_scope.value, TimeScope.EXPLICIT_RANGE)
        self.assertEqual(
            result.constraints.time_window.value.model_dump(),
            {"start": "18:30", "end": "22:30"},
        )
        self.assertEqual(
            result.constraints.time_window.rule_id,
            "time.window.from_departure_overrides_scope.v1",
        )
        self.assertTrue(
            any("精确出发时刻" in assumption.reason for assumption in result.assumptions)
        )

    def test_exact_departure_uses_explicit_return_deadline_as_window_end(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                time_text="下午",
                time_scope=TimeScope.AFTERNOON,
                departure_at_text="下午六点半准时出发",
                return_by_text="晚上八点前回来",
            ),
            evidence_map={
                "time_text": "下午",
                "departure_at_text": "下午六点半准时出发",
                "return_by_text": "20:00前回家",
            },
            extraction_confidence={
                "time_text": 1.0,
                "departure_at_text": 1.0,
                "return_by_text": 1.0,
            },
        )
        actor = ActorContext(user_id="demo", session_id="departure-scope-return", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.departure_at.value, "18:30")
        self.assertEqual(result.constraints.return_by.value, "20:00")
        self.assertEqual(
            result.constraints.time_window.value.model_dump(),
            {"start": "18:30", "end": "20:00"},
        )

    def test_exact_departure_does_not_override_explicit_time_range(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                time_text="10:00-16:00",
                time_scope=TimeScope.EXPLICIT_RANGE,
                departure_at_text="下午六点半准时出发",
            ),
            evidence_map={
                "time_text": "10:00-16:00",
                "departure_at_text": "下午六点半准时出发",
            },
            extraction_confidence={"time_text": 1.0, "departure_at_text": 1.0},
        )
        actor = ActorContext(user_id="demo", session_id="departure-explicit-range", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.time_scope.value, TimeScope.EXPLICIT_RANGE)
        self.assertEqual(
            result.constraints.time_window.value.model_dump(),
            {"start": "10:00", "end": "16:00"},
        )

    def test_departure_without_an_end_uses_a_visible_default_end(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(departure_at_text="14:30 出发"),
        )
        actor = ActorContext(user_id="demo", session_id="departure-default", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.time_window.value.model_dump(), {"start": "14:30", "end": "18:30"})
        self.assertIn("time_window", {item.field for item in result.assumptions})

    def test_all_day_compiles_to_visible_policy_window_instead_of_afternoon(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_text="明天",
                time_text="一整天",
                time_scope=TimeScope.ALL_DAY,
            ),
            evidence_map={"time_text": "一整天"},
            extraction_confidence={"time_text": 1.0},
        )
        actor = ActorContext(user_id="demo", session_id="all-day", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.time_scope.value, TimeScope.ALL_DAY)
        self.assertEqual(result.constraints.time_window.value.model_dump(), {"start": "09:00", "end": "21:00"})
        self.assertEqual(result.constraints.time_window.source, ConstraintSource.DEFAULT_RULE)
        self.assertIn("全天", result.assumptions[0].reason)

    def test_dinner_only_infers_an_evening_window_without_overriding_explicit_time(self) -> None:
        actor = ActorContext(user_id="demo", session_id="dinner", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )
        dinner_only = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(exact_stop_count=1, required_stop_roles=(StopRole.DINNER,)),
            evidence_map={"exact_stop_count": "只安排一家", "required_stop_roles": "晚饭"},
        )
        explicit_afternoon = dinner_only.model_copy(
            update={"raw_constraints": dinner_only.raw_constraints.model_copy(update={"time_text": "下午"})}
        )

        inferred = EnrichmentService().enrich(dinner_only, actor, environment).constraints
        explicit = EnrichmentService().enrich(explicit_afternoon, actor, environment).constraints

        self.assertEqual(inferred.time_window.value.model_dump(), {"start": "18:00", "end": "22:00"})
        self.assertEqual(inferred.time_window.source, ConstraintSource.USER_INFERRED)
        self.assertEqual(inferred.exact_stop_count.raw_text, "只安排一家")
        self.assertEqual(inferred.required_stop_roles.value, (StopRole.DINNER,))
        self.assertEqual(explicit.time_window.value.model_dump(), {"start": "14:00", "end": "18:00"})

    def test_multi_role_dinner_requirement_uses_a_meal_compatible_default_window(self) -> None:
        actor = ActorContext(user_id="demo", session_id="multi-role", identity_type=IdentityType.DEMO)
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
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                required_stop_roles=(StopRole.ACTIVITY, StopRole.DINNER),
            ),
            evidence_map={"required_stop_roles": "活动和晚饭"},
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(
            result.constraints.time_window.value.model_dump(),
            {"start": "14:00", "end": "21:00"},
        )
        self.assertEqual(
            result.constraints.time_window.source,
            ConstraintSource.DEFAULT_RULE,
        )
        self.assertTrue(
            any(item.rule_id == "time.multi_role.meal_anchor.v1" for item in result.assumptions)
        )
        self.assertEqual(
            result.constraints.required_stop_roles.value,
            (StopRole.ACTIVITY, StopRole.DINNER),
        )

    def test_multi_role_meal_defaults_preserve_order_and_return_deadline(self) -> None:
        actor = ActorContext(user_id="demo", session_id="multi-role-order", identity_type=IdentityType.DEMO)
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
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                time_text="下午",
                time_scope=TimeScope.AFTERNOON,
                required_stop_roles=(StopRole.ACTIVITY, StopRole.DINNER),
                return_by="20:00",
            ),
            evidence_map={
                "time_text": "下午",
                "required_stop_roles": "活动和晚饭",
                "return_by_text": "20:00前回家",
            },
            extraction_confidence={
                "time_text": 1.0,
                "required_stop_roles": 1.0,
                "return_by_text": 1.0,
            },
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(
            result.constraints.time_window.value.model_dump(),
            {"start": "14:00", "end": "20:00"},
        )
        self.assertEqual(result.constraints.return_by.value, "20:00")

    def test_multi_role_lunch_defaults_follow_role_order(self) -> None:
        actor = ActorContext(user_id="demo", session_id="multi-role-lunch", identity_type=IdentityType.DEMO)
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

        def enrich_roles(roles: tuple[StopRole, ...]) -> NormalizedConstraints:
            interpretation = Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                raw_constraints=RawConstraints(required_stop_roles=roles),
                evidence_map={"required_stop_roles": "、".join(role.value for role in roles)},
            )
            return EnrichmentService().enrich(interpretation, actor, environment).constraints

        activity_lunch = enrich_roles((StopRole.ACTIVITY, StopRole.LUNCH))
        lunch_activity = enrich_roles((StopRole.LUNCH, StopRole.ACTIVITY))

        self.assertEqual(
            activity_lunch.time_window.value.model_dump(),
            {"start": "09:00", "end": "14:00"},
        )
        self.assertEqual(
            lunch_activity.time_window.value.model_dump(),
            {"start": "11:30", "end": "18:00"},
        )

    def test_preserves_availability_confirmation_requirement(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                require_availability_confirmation=True,
            ),
            evidence_map={"require_availability_confirmation": "确认有位"},
        )
        actor = ActorContext(user_id="demo", session_id="availability", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertTrue(result.constraints.require_availability_confirmation)

    def test_lunch_only_infers_a_lunch_window_without_overriding_explicit_time(self) -> None:
        actor = ActorContext(user_id="demo", session_id="lunch", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", district="朝阳区", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )
        lunch_only = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                exact_stop_count=1,
                required_stop_roles=(StopRole.LUNCH,),
            ),
            evidence_map={"exact_stop_count": "只安排一顿", "required_stop_roles": "午饭"},
        )
        explicit_afternoon = lunch_only.model_copy(
            update={"raw_constraints": lunch_only.raw_constraints.model_copy(update={"time_text": "下午"})}
        )

        inferred = EnrichmentService().enrich(lunch_only, actor, environment).constraints
        explicit = EnrichmentService().enrich(explicit_afternoon, actor, environment).constraints

        self.assertEqual(inferred.time_window.value.model_dump(), {"start": "11:30", "end": "14:00"})
        self.assertEqual(inferred.time_window.source, ConstraintSource.USER_INFERRED)
        self.assertEqual(inferred.required_stop_roles.value, (StopRole.LUNCH,))
        self.assertEqual(explicit.time_window.value.model_dump(), {"start": "14:00", "end": "18:00"})

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

    def test_inferred_party_requires_an_extracted_party_value(self) -> None:
        with self.assertRaises(ValidationError):
            Interpretation(
                primary_intent=Intent.PLAN_OUTING,
                intent_scores={Intent.PLAN_OUTING: 1.0},
                evidence_map={"party": "约会"},
                extraction_confidence={"party": 0.85},
                inferred_fields={"party"},
            )

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

    def test_compiles_structured_tonight_reference_before_legacy_text(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_text="今晚",
                date_reference=DateReference.TODAY,
                time_text="今晚",
                time_scope=TimeScope.EVENING,
            ),
            evidence_map={"date_text": "今晚", "time_text": "今晚"},
            extraction_confidence={"date_text": 1.0, "time_text": 1.0},
        )
        actor = ActorContext(user_id="demo", session_id="tonight", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.date.value, date(2026, 8, 12))
        self.assertEqual(result.constraints.date.rule_id, "date.reference.today.v1")
        self.assertEqual(result.constraints.time_window.value.model_dump(), {"start": "18:00", "end": "22:00"})

    def test_compiles_structured_weekday_and_absolute_dates(self) -> None:
        current = date(2026, 8, 12)
        self.assertEqual(
            EnrichmentService._normalize_date("本周六", current),
            date(2026, 8, 15),
        )
        self.assertEqual(
            EnrichmentService._normalize_date("下周六", current),
            date(2026, 8, 22),
        )
        actor = ActorContext(user_id="demo", session_id="absolute-date", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                date_text="2026-09-01",
                date_reference=DateReference.ABSOLUTE,
                absolute_date=date(2026, 9, 1),
            ),
            evidence_map={"date_text": "2026-09-01"},
            extraction_confidence={"date_text": 1.0},
        )
        self.assertEqual(
            EnrichmentService().enrich(interpretation, actor, environment).constraints.date.value,
            date(2026, 9, 1),
        )

    def test_structured_explicit_window_is_not_overridden_by_departure(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(
                time_text="10:00–16:00",
                time_scope=TimeScope.EXPLICIT_RANGE,
                explicit_time_window=TimeWindow(start="10:00", end="16:00"),
                departure_at_text="下午六点半准时出发",
            ),
            evidence_map={
                "time_text": "10:00–16:00",
                "departure_at_text": "下午六点半准时出发",
            },
            extraction_confidence={"time_text": 1.0, "departure_at_text": 1.0},
        )
        actor = ActorContext(user_id="demo", session_id="explicit-window", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.time_scope.value, TimeScope.EXPLICIT_RANGE)
        self.assertEqual(result.constraints.time_window.value.model_dump(), {"start": "10:00", "end": "16:00"})

    def test_invalid_structured_temporal_contract_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            RawConstraints(date_reference=DateReference.WEEKDAY)
        with self.assertRaises(ValidationError):
            RawConstraints(date_reference=DateReference.TODAY, weekday=Weekday.SATURDAY)
        with self.assertRaises(ValidationError):
            RawConstraints(
                time_scope=TimeScope.EXPLICIT_RANGE,
                explicit_time_window=TimeWindow(start="16:00", end="10:00"),
            )

    def test_unresolved_legacy_tonight_text_is_still_supported(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 1.0},
            raw_constraints=RawConstraints(date_text="今晚", time_text="今晚"),
        )
        actor = ActorContext(user_id="demo", session_id="legacy-tonight", identity_type=IdentityType.DEMO)
        environment = EnvironmentContext(
            now=datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(city="北京市", address="北京市朝阳区", latitude=39.9219, longitude=116.4436),
        )

        result = EnrichmentService().enrich(interpretation, actor, environment)

        self.assertEqual(result.constraints.date.value, date(2026, 8, 12))
        self.assertEqual(result.constraints.time_window.value.model_dump(), {"start": "18:00", "end": "22:00"})

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

        resolved = EnrichmentService(
            MockGeocodingProvider.from_locations(
                {
                    ("北京市", "海淀黄庄附近"): (
                        39.9756,
                        116.3176,
                        "海淀区",
                        "110108",
                        "北京市海淀区黄庄",
                    )
                }
            )
        ).enrich(
            interpretation,
            actor,
            environment,
        )

        self.assertEqual(resolved.constraints.location.value.district, "海淀区")
        self.assertEqual(resolved.constraints.location.value.adcode, "110108")
        self.assertEqual(resolved.constraints.location.source, ConstraintSource.REAL_TOOL)

    def test_return_by_is_normalized_from_clock_and_text(self) -> None:
        self.assertEqual(
            EnrichmentService._normalize_return_by("最晚18:00到家", None),
            "18:00",
        )
        self.assertEqual(
            EnrichmentService._normalize_return_by(None, "19:30"),
            "19:30",
        )
        self.assertIsNone(EnrichmentService._normalize_return_by("路上再说", None))
        self.assertIsNone(EnrichmentService._normalize_return_by(None, "25:00"))

    def test_return_by_and_total_distance_flow_into_normalized_constraints(self) -> None:
        interpretation = Interpretation(
            primary_intent=Intent.PLAN_OUTING,
            intent_scores={Intent.PLAN_OUTING: 0.95},
            raw_constraints=RawConstraints(
                return_by_text="最晚18:30到家",
                total_distance_km=12.5,
            ),
        )
        actor = ActorContext(
            user_id="demo-user",
            session_id="session-return",
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

        self.assertEqual(result.constraints.return_by.value, "18:30")
        self.assertEqual(
            result.constraints.return_by.source,
            ConstraintSource.USER_INFERRED,
        )
        self.assertEqual(result.constraints.total_distance_km.value, 12.5)
        self.assertEqual(
            result.constraints.total_distance_km.source,
            ConstraintSource.USER_EXPLICIT,
        )


if __name__ == "__main__":
    unittest.main()
