import unittest
from datetime import date, datetime, timezone

from pydantic import ValidationError

from app.domain.catalog import (
    CatalogWarningCode,
    CatalogSource,
    PriceKind,
    ResourceType,
    StopCandidate,
    VerificationStatus,
    ViolationCode,
)
from app.domain.constraints import (
    ConstraintSource,
    ConstraintValue,
    GeoLocation,
    NormalizedConstraints,
    PartyProfile,
    TimeWindow,
)
from app.domain.providers import GeoPoint
from app.services.catalog import InMemoryCatalog, LocalFixtureCatalog


SOURCE = CatalogSource(
    source_name="HappyFreeTime local test fixture",
    source_uri="repo://tests/test_catalog.py",
    source_license="project-test-fixture",
    collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    verification_status=VerificationStatus.UNVERIFIED,
)


def constraints() -> NormalizedConstraints:
    return NormalizedConstraints(
        date=ConstraintValue[date](
            value=date(2026, 8, 15),
            source=ConstraintSource.USER_EXPLICIT,
        ),
        time_window=ConstraintValue[TimeWindow](
            value=TimeWindow(start="14:00", end="18:00"),
            source=ConstraintSource.USER_EXPLICIT,
        ),
        location=ConstraintValue[GeoLocation](
            value=GeoLocation(
                city="北京市",
                district="朝阳区",
                address="北京市朝阳区",
                latitude=39.9219,
                longitude=116.4436,
            ),
            source=ConstraintSource.SYSTEM_CONTEXT,
        ),
        party=ConstraintValue[PartyProfile](
            value=PartyProfile(adults=2, children=1, child_age=6),
            source=ConstraintSource.USER_EXPLICIT,
        ),
        budget_per_person=ConstraintValue[int](
            value=100,
            source=ConstraintSource.USER_EXPLICIT,
        ),
        max_distance_km=ConstraintValue[float](
            value=8,
            source=ConstraintSource.USER_EXPLICIT,
        ),
        strict_budget=True,
    )


def candidate(
    resource_id: str,
    *,
    avg_price: int | None = 80,
    price_kind: PriceKind = PriceKind.KNOWN,
    max_party_size: int | None = 6,
    child_age_min: int | None = 3,
    child_age_max: int | None = 8,
    open_hours: dict[str, str] | None = None,
    longitude: float = 116.44,
) -> StopCandidate:
    return StopCandidate(
        resource_id=resource_id,
        resource_type=ResourceType.ACTIVITY,
        name=resource_id,
        district="朝阳区",
        address="北京市朝阳区",
        location=GeoPoint(latitude=39.92, longitude=longitude),
        avg_price=avg_price,
        price_kind=price_kind,
        duration_minutes=60,
        open_hours=open_hours or {"sat": "10:00-20:00"},
        max_party_size=max_party_size,
        children_allowed=True,
        child_age_min=child_age_min,
        child_age_max=child_age_max,
        source=SOURCE,
    )


class CatalogTest(unittest.TestCase):
    def test_unsupported_opening_hours_are_kept_with_an_unverified_warning(self) -> None:
        unsupported = candidate(
            "seasonal-hours",
            open_hours={"sat": "sunrise-sunset"},
        )

        result = InMemoryCatalog([unsupported]).recall(constraints())

        self.assertEqual([item.resource_id for item in result.candidates], ["seasonal-hours"])
        self.assertEqual(result.violations, [])
        self.assertEqual(
            [warning.code for warning in result.warnings],
            [CatalogWarningCode.OPENING_HOURS_UNVERIFIED],
        )

    def test_unknown_hours_and_child_suitability_are_warnings_not_mocked_or_pruned(self) -> None:
        unknown = candidate("unknown-facts").model_copy(
            update={"children_allowed": None, "open_hours": {}}
        )

        result = InMemoryCatalog([unknown]).recall(constraints())

        self.assertEqual([item.resource_id for item in result.candidates], ["unknown-facts"])
        self.assertEqual(result.violations, [])
        self.assertEqual(
            {warning.code for warning in result.warnings},
            {
                CatalogWarningCode.OPENING_HOURS_UNVERIFIED,
                CatalogWarningCode.CHILD_SUITABILITY_UNVERIFIED,
            },
        )

    def test_strict_budget_rejects_estimated_or_unknown_prices(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("known", avg_price=80, price_kind=PriceKind.KNOWN),
                candidate("estimated", avg_price=60, price_kind=PriceKind.ESTIMATED),
                candidate("unknown", avg_price=None, price_kind=PriceKind.UNKNOWN),
            ]
        )

        result = catalog.recall(constraints())

        self.assertEqual([item.resource_id for item in result.candidates], ["known"])
        self.assertEqual(
            {item.resource_id: item.code for item in result.violations},
            {
                "estimated": ViolationCode.SINGLE_RESOURCE_PRICE_UNVERIFIED,
                "unknown": ViolationCode.SINGLE_RESOURCE_PRICE_UNVERIFIED,
            },
        )

    def test_unknown_price_legacy_bridge_keeps_unknown_semantics(self) -> None:
        record = candidate(
            "unknown",
            avg_price=None,
            price_kind=PriceKind.UNKNOWN,
        ).to_legacy_record()

        self.assertEqual(record["avg_price"], 0)
        self.assertEqual(record["price_kind"], "unknown")

    def test_candidate_without_source_metadata_is_rejected(self) -> None:
        payload = candidate("missing_source").model_dump(exclude={"source"})

        with self.assertRaises(ValidationError):
            StopCandidate.model_validate(payload)

    def test_recall_prunes_single_resource_hard_constraint_violations(self) -> None:
        catalog = InMemoryCatalog(
            [
                candidate("eligible"),
                candidate("over_budget", avg_price=120),
                candidate("too_small", max_party_size=2),
                candidate("wrong_age", child_age_min=10, child_age_max=15),
                candidate("closed", open_hours={"sat": "20:00-22:00"}),
                candidate("too_far", longitude=116.60),
            ]
        )

        result = catalog.recall(constraints())

        self.assertEqual(
            [item.resource_id for item in result.candidates],
            ["eligible"],
        )
        self.assertEqual(
            {item.code for item in result.violations},
            {
                ViolationCode.SINGLE_RESOURCE_BUDGET_EXCEEDED,
                ViolationCode.PARTY_TOO_LARGE,
                ViolationCode.CHILD_AGE_NOT_SUPPORTED,
                ViolationCode.OUTSIDE_BASIC_OPENING_HOURS,
                ViolationCode.OUTSIDE_RECALL_RADIUS,
            },
        )
        self.assertTrue(
            all(item.source.verification_status == VerificationStatus.UNVERIFIED for item in result.candidates)
        )

    def test_local_fixture_candidates_expose_honest_source_metadata(self) -> None:
        unconstrained = constraints().model_copy(
            update={
                "strict_budget": False,
                "party": ConstraintValue[PartyProfile](
                    value=PartyProfile(adults=1),
                    source=ConstraintSource.USER_EXPLICIT,
                ),
                "max_distance_km": ConstraintValue[float](
                    value=50,
                    source=ConstraintSource.USER_EXPLICIT,
                ),
            }
        )

        result = LocalFixtureCatalog().recall(unconstrained)

        self.assertEqual(len(result.candidates), 8)
        self.assertEqual(result.violations, [])
        for item in result.candidates:
            self.assertTrue(item.source.source_uri.startswith("repo://data/fixtures/v1/"))
            self.assertEqual(
                item.source.verification_status,
                VerificationStatus.UNVERIFIED,
            )
            self.assertIsNone(item.source.last_verified_at)


if __name__ == "__main__":
    unittest.main()
