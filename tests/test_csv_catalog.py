import csv
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from app.domain.catalog import CoordinateSystem, PriceKind, ResourceType
from app.domain.constraints import NormalizedConstraints
from app.services.catalog import CatalogDataError, CsvCatalog
from tests.test_catalog import constraints


FIELDS = [
    "resource_id",
    "resource_type",
    "name",
    "category_tags",
    "district",
    "address",
    "latitude",
    "longitude",
    "coordinate_system",
    "duration_minutes",
    "weather_sensitive",
    "avg_price_yuan",
    "price_kind",
    "max_party_size",
    "children_allowed",
    "child_age_min",
    "child_age_max",
    "booking_required",
    "reservation_required",
    "open_mon",
    "open_tue",
    "open_wed",
    "open_thu",
    "open_fri",
    "open_sat",
    "open_sun",
    "source_name",
    "source_uri",
    "source_license",
    "collected_at",
    "last_verified_at",
    "verification_status",
    "image_url",
    "image_source_uri",
    "image_author",
    "image_license",
    "image_license_uri",
    "image_attribution",
    "notes",
]


def valid_row(**updates: str) -> dict[str, str]:
    row = {
        "resource_id": "osm-node-1",
        "resource_type": "activity",
        "name": "测试美术馆",
        "category_tags": "室内|美术馆",
        "district": "朝阳区",
        "address": "北京市朝阳区测试路1号",
        "latitude": "39.9200",
        "longitude": "116.4400",
        "coordinate_system": "WGS84",
        "duration_minutes": "90",
        "weather_sensitive": "false",
        "avg_price_yuan": "30",
        "price_kind": "known",
        "max_party_size": "6",
        "children_allowed": "true",
        "child_age_min": "3",
        "child_age_max": "12",
        "booking_required": "false",
        "reservation_required": "false",
        "open_mon": "10:00-20:00",
        "open_tue": "10:00-20:00",
        "open_wed": "10:00-20:00",
        "open_thu": "10:00-20:00",
        "open_fri": "10:00-20:00",
        "open_sat": "10:00-20:00",
        "open_sun": "10:00-20:00",
        "source_name": "OpenStreetMap contributors",
        "source_uri": "https://www.openstreetmap.org/node/1",
        "source_license": "ODbL-1.0",
        "collected_at": "2026-08-23T09:00:00+08:00",
        "last_verified_at": "",
        "verification_status": "unverified",
        "image_url": "https://commons.wikimedia.org/example.jpg",
        "image_source_uri": "https://commons.wikimedia.org/wiki/File:Example.jpg",
        "image_author": "Example author",
        "image_license": "CC-BY-SA-4.0",
        "image_license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
        "image_attribution": "Example author / CC BY-SA 4.0",
        "notes": "test row",
    }
    row.update(updates)
    return row


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class CsvCatalogTest(unittest.TestCase):
    def test_repository_catalog_has_balanced_source_traceable_beijing_coverage(self) -> None:
        candidates = CsvCatalog().recall(NormalizedConstraints()).candidates

        self.assertEqual(len(candidates), 36)
        self.assertEqual(
            Counter(item.resource_type for item in candidates),
            {ResourceType.ACTIVITY: 18, ResourceType.RESTAURANT: 18},
        )
        self.assertTrue(
            all(item.source.source_license == "ODbL-1.0" for item in candidates)
        )
        self.assertTrue(all(item.source.source_uri.startswith("https://www.openstreetmap.org/") for item in candidates))
        self.assertTrue(all(item.children_allowed is None for item in candidates))
        self.assertTrue(all(not item.source.dynamic_fields_mock for item in candidates))
        self.assertTrue(all("duration_minutes" in item.source.estimated_fields for item in candidates))
        self.assertTrue(all("avg_price" in item.source.estimated_fields for item in candidates))
        licensed_images = [item.image for item in candidates if item.image]
        self.assertEqual(len(licensed_images), 1)
        self.assertEqual(licensed_images[0].license, "CC-BY-2.0")
        self.assertEqual(
            licensed_images[0].license_uri,
            "https://creativecommons.org/licenses/by/2.0/",
        )

    def test_loads_offline_csv_with_provenance_optional_image_and_gcj02_location(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pois.csv"
            write_csv(path, [valid_row()])

            result = CsvCatalog(path).recall(
                constraints().model_copy(update={"strict_budget": False})
            )

        self.assertEqual(len(result.candidates), 1)
        item = result.candidates[0]
        self.assertEqual(item.resource_type, ResourceType.ACTIVITY)
        self.assertEqual(item.category_tags, ["室内", "美术馆"])
        self.assertEqual(item.price_kind, PriceKind.KNOWN)
        self.assertEqual(item.coordinate_system, CoordinateSystem.GCJ02)
        self.assertEqual(item.source.coordinate_system, CoordinateSystem.WGS84)
        self.assertEqual(item.source.source_license, "ODbL-1.0")
        self.assertEqual(item.image.license, "CC-BY-SA-4.0")
        self.assertGreater(item.location.longitude, 116.44)

    def test_missing_image_is_valid_and_does_not_affect_pruning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pois.csv"
            write_csv(
                path,
                [
                    valid_row(
                        image_url="",
                        image_source_uri="",
                        image_author="",
                        image_license="",
                        image_license_uri="",
                        image_attribution="",
                    )
                ],
            )

            result = CsvCatalog(path).recall(
                constraints().model_copy(update={"strict_budget": False})
            )

        self.assertEqual(len(result.candidates), 1)
        self.assertIsNone(result.candidates[0].image)

    def test_rejects_duplicate_ids_and_invalid_source_coordinates_hours_or_image_license(self) -> None:
        invalid_cases = {
            "duplicate resource_id": [valid_row(), valid_row()],
            "source_name": [valid_row(source_name="")],
            "latitude": [valid_row(latitude="91")],
            "open_sat": [valid_row(open_sat="weekends")],
            "open_sun": [valid_row(open_sun="")],
            "image_license": [valid_row(image_license="")],
        }
        for expected, rows in invalid_cases.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "pois.csv"
                write_csv(path, rows)
                with self.assertRaisesRegex(CatalogDataError, expected):
                    CsvCatalog(path)

    def test_unknown_price_cannot_be_encoded_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pois.csv"
            write_csv(path, [valid_row(price_kind="unknown", avg_price_yuan="0")])

            with self.assertRaisesRegex(CatalogDataError, "avg_price_yuan"):
                CsvCatalog(path)


if __name__ == "__main__":
    unittest.main()
