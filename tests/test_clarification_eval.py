"""Deterministic field-recovery eval for the shared request-patch path."""

from datetime import date, datetime
import unittest
from zoneinfo import ZoneInfo

from app.domain.constraints import (
    ActorContext,
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    IdentityType,
    PartyProfile,
    PlanRequest,
    RequestPatch,
)
from app.providers.geocoding import MockGeocodingProvider
from app.services.clarification_patch import (
    ClarificationPatchCompiler,
    merge_request_patches,
)
from app.services.constraint_engine import (
    ConflictedRequest,
    ConstraintEngine,
    ResolvedRequest,
)
from app.services.constraint_patch import ConstraintPatchProposalCompiler
from app.services.enrichment import EnvironmentContext


class ClarificationRecoveryEvalTest(unittest.TestCase):
    """24 fixed answer/default cases exercise one field-scoped resume each."""

    def setUp(self) -> None:
        self.environment = EnvironmentContext(
            now=datetime(2026, 10, 3, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            default_location=GeoLocation(
                city="北京市", district="朝阳区", address="北京市朝阳区",
                latitude=39.9219, longitude=116.4436,
            ),
        )
        geocoder = MockGeocodingProvider.from_locations(
            {("北京市", "朝阳公园"): (39.93, 116.44, "朝阳区", "110105", "北京市朝阳区朝阳公园")}
        )
        self.proposal_compiler = ConstraintPatchProposalCompiler(
            geocoding_provider=geocoder,
        )
        self.compiler = ClarificationPatchCompiler(self.proposal_compiler)
        self.engine = ConstraintEngine()
        self.actor = ActorContext(
            user_id="clarification-eval",
            session_id="clarification-eval",
            identity_type=IdentityType.DEMO,
        )

    def test_24_deterministic_field_recovery_cases(self) -> None:
        cases = [
            ("budget Arabic", "budget_per_person", "人均200元", "answer", PlanRequest(), lambda r: r.budget_per_person.value == 200),
            ("budget Chinese", "budget_per_person", "人均三百元", "answer", PlanRequest(), lambda r: r.budget_per_person.value == 300),
            ("date explicit", "date", "明天", "answer", PlanRequest(), lambda r: r.planning_window.date.value == date(2026, 10, 4)),
            ("date default", "date", None, "default", PlanRequest(), lambda r: r.planning_window.date.source == ConstraintSource.DEFAULT_RULE),
            ("departure exact phrase", "departure_at", "早上九点出发", "answer", PlanRequest(), lambda r: r.planning_window.start_at.value == "09:00"),
            ("departure bare clock", "departure_at", "9:00", "answer", PlanRequest(), lambda r: r.planning_window.start_at.value == "09:00"),
            ("departure default", "departure_at", None, "default", PlanRequest(), lambda r: r.planning_window.start_at.value == "09:00"),
            ("return phrase", "return_by", "晚上八点前回家", "answer", PlanRequest(), lambda r: r.planning_window.end_at.value == "20:00"),
            ("return bare clock", "return_by", "18:00", "answer", PlanRequest(), lambda r: r.planning_window.end_at.value == "18:00"),
            ("trip period", "time_window", "下午", "answer", PlanRequest(), lambda r: (r.planning_window.start_at.value, r.planning_window.end_at.value) == ("14:00", "18:00")),
            ("trip default", "time_window", None, "default", PlanRequest(), lambda r: (r.planning_window.start_at.value, r.planning_window.end_at.value) == ("14:00", "18:00")),
            ("location geocoded", "location", "朝阳公园", "answer", PlanRequest(), lambda r: r.location.value.address.endswith("朝阳公园")),
            ("location default", "location", None, "default", PlanRequest(), lambda r: r.location.source == ConstraintSource.SYSTEM_CONTEXT),
            ("distance answer", "max_distance_km", "最多8公里", "answer", PlanRequest(), lambda r: r.max_distance_km.value == 8.0),
            ("distance default clears", "max_distance_km", None, "default", PlanRequest(max_distance_km=ConstraintValue(value=8.0, source=ConstraintSource.DEFAULT_RULE)), lambda r: r.max_distance_km is None),
            ("total distance answer", "total_distance_km", "全程12公里", "answer", PlanRequest(), lambda r: r.total_distance_km.value == 12.0),
            ("total distance default clears", "total_distance_km", None, "default", PlanRequest(total_distance_km=ConstraintValue(value=12.0, source=ConstraintSource.USER_EXPLICIT)), lambda r: r.total_distance_km is None),
            ("preference append", "preferences", "安静", "answer", PlanRequest(), lambda r: r.preferences == ["安静"]),
            ("preference preserves prior patch", "preferences", "适合聊天", "answer", PlanRequest(), lambda r: r.preferences == ["轻松", "适合聊天"], {"preferences": ["轻松"]}),
            ("diet tag append", "diet_tags", "清淡", "answer", PlanRequest(), lambda r: r.diet_tags == ["清淡"]),
            ("avoid append", "avoid", "不吃辣", "answer", PlanRequest(), lambda r: r.avoid == ["不吃辣"]),
            ("party count", "party", "2", "answer", PlanRequest(), lambda r: r.party.value.adults == 2),
            ("party adults and children", "party", "2位成人1个孩子", "answer", PlanRequest(), lambda r: (r.party.value.adults, r.party.value.children) == (2, 1)),
            ("child age", "child_age", "六岁", "answer", PlanRequest(party=ConstraintValue(value=PartyProfile(children=1), source=ConstraintSource.USER_EXPLICIT)), lambda r: r.party.value.child_age == 6),
        ]
        self.assertEqual(len(cases), 24)
        successful = 0

        for case in cases:
            label, field, answer, mode, request, expected = case[:6]
            existing_fields = case[6] if len(case) == 7 else {}
            with self.subTest(case=label):
                if mode == "default":
                    patch = self.compiler.compile_default(
                        field=field,
                        request=request,
                        environment=self.environment,
                    )
                else:
                    patch = self.compiler.compile_answer(
                        field=field,
                        value=answer,
                        request=request,
                        actor=self.actor,
                        environment=self.environment,
                    )
                self.assertIsNotNone(patch)
                pending = RequestPatch(
                    base_revision=request.revision,
                    set_fields=existing_fields,
                    source=ConstraintSource.USER_EXPLICIT,
                )
                merged = merge_request_patches(pending, patch)
                result = self.engine.apply(request, merged)
                self.assertIsInstance(result, ResolvedRequest)
                self.assertTrue(expected(result.request))
                successful += 1

        # The recovery-success numerator/denominator are explicit so this
        # deterministic suite can be compared as the clarification path evolves.
        self.assertEqual(successful, 24)

    def test_invalid_multifield_patch_is_atomic(self) -> None:
        request = PlanRequest(preferences=["轻松"])
        patch = RequestPatch(
            base_revision=0,
            set_fields={"preferences": ["安静"], "budget_per_person": "not-an-integer"},
            source=ConstraintSource.USER_EXPLICIT,
        )
        result = self.engine.apply(request, patch)
        self.assertIsInstance(result, ConflictedRequest)
        self.assertEqual(request.preferences, ["轻松"])
        self.assertEqual(request.revision, 0)
