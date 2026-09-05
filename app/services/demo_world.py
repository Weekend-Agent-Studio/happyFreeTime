"""Versioned deterministic Demo World adapter and POI presentation provider."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.catalog import (
    CatalogResult,
    CatalogSource,
    PriceKind,
    StopCandidate,
    VerificationStatus,
)
from app.domain.presentation import PoiGalleryItem, PoiPresentation
from app.domain.constraints import NormalizedConstraints
from app.services.catalog import CatalogDataError, InMemoryCatalog, SnapshotCatalog


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_WORLD = _ROOT / "data" / "demo_world" / "v1" / "enrichment.json"


class DemoWorld:
    """Loads generated enrichment; no values are generated at request time."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_WORLD
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            self.manifest = payload["manifest"]
            records = payload["records"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise CatalogDataError(f"cannot read Demo World {self._path}: {exc}") from exc
        if self.manifest.get("schema_version") != 1:
            raise CatalogDataError(f"{self._path}: unsupported schema_version")
        if self.manifest.get("record_count") != len(records):
            raise CatalogDataError(f"{self._path}: manifest record_count does not match records")
        self._records = {record.get("resource_id"): record for record in records}
        if len(self._records) != len(records) or None in self._records:
            raise CatalogDataError(f"{self._path}: resource_id values must be unique and non-empty")

    def record(self, resource_id: str) -> dict:
        try:
            return self._records[resource_id]
        except KeyError as exc:
            raise LookupError(f"Demo World has no enrichment for {resource_id}") from exc

    def present_many(self, resource_ids: list[str]) -> list[PoiPresentation]:
        result: list[PoiPresentation] = []
        seen: set[str] = set()
        for resource_id in resource_ids:
            if resource_id in seen:
                continue
            seen.add(resource_id)
            record = self._records.get(resource_id)
            if record is None:
                continue
            gallery = [PoiGalleryItem.model_validate(item) for item in record["gallery"]]
            result.append(
                PoiPresentation(
                    resource_id=resource_id,
                    name=record["name"],
                    category_label=record["category_label"],
                    business_area=record["business_area"],
                    address=record["address_display"],
                    description=record["description"],
                    scene_tags=record["scene_tags"],
                    facility_tags=record["facility_tags"],
                    indoor=record["indoor"],
                    weather_suitability=record["weather_suitability"],
                    child_suitability=record["child_suitability"],
                    reference_avg_price=record["reference_avg_price"],
                    demo_rating=record["reference_rating"],
                    demo_review_count=record["reference_review_count"],
                    opening_hours_display=record["opening_hours_display"],
                    opening_status="已纳入本次营业时间校验",
                    suggested_duration_minutes=record["suggested_duration_minutes"],
                    reservation_requirement=record["reservation_requirement"],
                    queue_profile=record["queue_profile"],
                    risk_tips=record["risk_tips"],
                    booking_mode=record["booking_mode"],
                    gallery=gallery,
                    data_notice="演示环境：POI 商业信息为模拟数据；路线来源和降级状态见各路线段。",
                )
            )
        return result


class DemoCatalog:
    """Catalog adapter: OSM anchors plus deterministic Demo World enrichment."""

    def __init__(self, *, snapshot: SnapshotCatalog | None = None, world: DemoWorld | None = None) -> None:
        self._snapshot = snapshot or SnapshotCatalog()
        self._world = world or DemoWorld()
        anchors = self._snapshot.load_candidates()
        enriched = [self._merge(candidate, self._world.record(candidate.resource_id)) for candidate in anchors]
        self._delegate = InMemoryCatalog(enriched)

    @property
    def presentation_provider(self) -> DemoWorld:
        return self._world

    def recall(self, constraints: NormalizedConstraints) -> CatalogResult:
        return self._delegate.recall(constraints)

    @staticmethod
    def _merge(anchor: StopCandidate, record: dict) -> StopCandidate:
        if record["name"] != anchor.name:
            raise CatalogDataError(f"Demo World name mismatch for {anchor.resource_id}")
        source = CatalogSource(
            source_name="HappyFreeTime Demo World V1（OSM 地点锚点）",
            source_uri="data/demo_world/v1/README.md",
            source_license="Demo business attributes; OSM anchors © OpenStreetMap contributors ODbL-1.0",
            collected_at="2026-08-31T00:00:00Z",
            verification_status=VerificationStatus.UNVERIFIED,
        )
        return anchor.model_copy(
            update={
                "district": record["district_display"],
                "address": record["address_display"],
                "preference_tags": record["preference_tags"],
                "scene_tags": record["scene_tags"],
                "avg_price": record["reference_avg_price"],
                # Known inside the deterministic Demo World.  It is never presented
                # as a live merchant price; its job is to make budget pruning testable.
                "price_kind": PriceKind.KNOWN,
                "duration_minutes": record["suggested_duration_minutes"],
                "open_hours": record["open_hours"],
                "max_party_size": record["max_party_size"],
                "children_allowed": record["children_allowed"],
                "child_age_min": record["child_age_min"],
                "child_age_max": record["child_age_max"],
                "weather_sensitive": record["weather_sensitive"],
                "booking_required": record["booking_required"],
                "reservation_required": record["reservation_required"],
                "source": source,
            }
        )
