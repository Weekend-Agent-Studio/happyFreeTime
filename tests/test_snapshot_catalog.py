import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.domain.catalog import CoordinateSystem
from app.domain.constraints import NormalizedConstraints
from app.services.catalog import CatalogDataError, SnapshotCatalog
from app.services.catalog_collection import CatalogCollector
from tests.test_catalog_collection import COLLECTED_AT


class SnapshotCatalogTest(unittest.TestCase):
    def test_rejects_manifest_count_mismatch_and_duplicate_resource_ids(self) -> None:
        snapshot = CatalogCollector(max_records=20).build(
            {
                "elements": [
                    {"type": "node", "id": 1, "lat": 39.9, "lon": 116.4,
                     "tags": {"name": "甲博物馆", "tourism": "museum"}},
                    {"type": "node", "id": 2, "lat": 39.91, "lon": 116.41,
                     "tags": {"name": "乙博物馆", "tourism": "museum"}},
                ]
            },
            collected_at=COLLECTED_AT,
        )
        broken_payloads = []
        count_mismatch = json.loads(json.dumps(snapshot))
        count_mismatch["manifest"]["record_count"] = 99
        broken_payloads.append(count_mismatch)
        duplicate = json.loads(json.dumps(snapshot))
        duplicate["records"][1]["resource_id"] = duplicate["records"][0]["resource_id"]
        broken_payloads.append(duplicate)

        for payload in broken_payloads:
            with self.subTest(payload=payload["manifest"]):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "pois.json"
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(CatalogDataError):
                        SnapshotCatalog(path)

    def test_repository_snapshot_is_balanced_unique_and_materially_complete(self) -> None:
        candidates = SnapshotCatalog().recall(NormalizedConstraints()).candidates

        self.assertEqual(len(candidates), 200)
        self.assertEqual(len({item.resource_id for item in candidates}), 200)
        self.assertEqual(sum(item.resource_type.value == "activity" for item in candidates), 100)
        self.assertEqual(sum(item.resource_type.value == "restaurant" for item in candidates), 100)
        self.assertGreaterEqual(sum(bool(item.address) for item in candidates), 90)
        self.assertGreaterEqual(sum(item.image is not None for item in candidates), 65)
        self.assertTrue(all(item.source.source_license == "ODbL-1.0" for item in candidates))
        self.assertTrue(all(item.avg_price is None for item in candidates))
        self.assertTrue(all(item.category_tags for item in candidates))

    def test_manifest_provenance_is_applied_without_per_field_source_lists(self) -> None:
        payload = {
            "elements": [
                {
                    "type": "node",
                    "id": 101,
                    "lat": 39.9,
                    "lon": 116.4,
                    "tags": {
                        "name": "测试博物馆",
                        "tourism": "museum",
                        "opening_hours": "Mo-Su 09:00-17:00",
                    },
                }
            ]
        }
        snapshot = CatalogCollector(max_records=20).build(
            payload,
            collected_at=COLLECTED_AT,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pois.json"
            path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            candidate = SnapshotCatalog(path).recall(NormalizedConstraints()).candidates[0]

        self.assertEqual(candidate.coordinate_system, CoordinateSystem.GCJ02)
        self.assertGreater(candidate.location.longitude, 116.4)
        self.assertEqual(candidate.source.source_name, "OpenStreetMap contributors")
        self.assertEqual(candidate.source.source_license, "ODbL-1.0")
        self.assertEqual(
            set(candidate.source.model_dump()),
            {
                "source_name",
                "source_uri",
                "source_license",
                "collected_at",
                "last_verified_at",
                "verification_status",
            },
        )
        self.assertEqual(
            candidate.source.collected_at,
            datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
