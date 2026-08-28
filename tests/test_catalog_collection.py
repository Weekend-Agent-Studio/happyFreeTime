import unittest
from datetime import datetime, timezone
from urllib.error import URLError

from app.services.catalog_collection import CatalogCollector, WikimediaImageResolver


COLLECTED_AT = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)


class CatalogCollectionTest(unittest.TestCase):
    def test_unsupported_opening_hours_exceptions_do_not_become_partial_truth(self) -> None:
        snapshot = CatalogCollector(max_records=20).build(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 405,
                        "lat": 39.9,
                        "lon": 116.4,
                        "tags": {
                            "name": "节假日例外餐厅",
                            "amenity": "restaurant",
                            "opening_hours": "Mo-Su 09:00-18:00; PH off",
                        },
                    }
                ]
            },
            collected_at=COLLECTED_AT,
        )

        self.assertEqual(snapshot["records"][0]["open_hours"], {})

    def test_preserves_multiple_opening_intervals_for_each_day(self) -> None:
        snapshot = CatalogCollector(max_records=20).build(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 404,
                        "lat": 39.9,
                        "lon": 116.4,
                        "tags": {
                            "name": "分时段营业餐厅",
                            "amenity": "restaurant",
                            "opening_hours": "Mo-Su 11:00-14:00,16:00-20:00",
                        },
                    }
                ]
            },
            collected_at=COLLECTED_AT,
        )

        self.assertEqual(
            snapshot["records"][0]["open_hours"]["sat"],
            "11:00-14:00,16:00-20:00",
        )

    def test_same_osm_object_from_multiple_queries_is_emitted_once(self) -> None:
        element = {
            "type": "node",
            "id": 101,
            "lat": 39.9,
            "lon": 116.4,
            "tags": {"name": "测试博物馆", "tourism": "museum"},
        }

        snapshot = CatalogCollector(max_records=20).build(
            {"elements": [element, element]},
            collected_at=COLLECTED_AT,
        )

        self.assertEqual(snapshot["manifest"]["record_count"], 1)
        self.assertEqual(len(snapshot["records"]), 1)

    def test_image_provider_failure_degrades_to_empty_images(self) -> None:
        def failing_get_json(_url: str, _params: dict[str, str]) -> dict:
            raise URLError("commons unavailable")

        by_wikidata, by_commons = WikimediaImageResolver(failing_get_json).resolve(
            {"elements": [{"tags": {"wikidata": "Q101"}}]}
        )

        self.assertEqual(by_wikidata, {})
        self.assertEqual(by_commons, {})

    def test_resolves_wikidata_images_with_commons_license_metadata(self) -> None:
        responses = [
            {
                "entities": {
                    "Q101": {
                        "claims": {
                            "P18": [
                                {"mainsnak": {"datavalue": {"value": "Example.jpg"}}}
                            ]
                        }
                    }
                }
            },
            {
                "query": {
                    "pages": {
                        "1": {
                            "title": "File:Example.jpg",
                            "imageinfo": [
                                {
                                    "thumburl": "https://upload.wikimedia.org/example-960.jpg",
                                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Example.jpg",
                                    "extmetadata": {
                                        "Artist": {"value": "Example author"},
                                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                                        "Credit": {"value": "Example credit"},
                                    },
                                }
                            ],
                        }
                    }
                }
            },
        ]

        def fake_get_json(_url: str, _params: dict[str, str]) -> dict:
            return responses.pop(0)

        by_wikidata, by_commons = WikimediaImageResolver(fake_get_json).resolve(
            {"elements": [{"tags": {"wikidata": "Q101"}}]}
        )

        self.assertEqual(by_commons, {})
        self.assertEqual(by_wikidata["Q101"]["license"], "CC BY-SA 4.0")
        self.assertEqual(
            by_wikidata["Q101"]["url"],
            "https://upload.wikimedia.org/example-960.jpg",
        )
        self.assertEqual(responses, [])

    def test_builds_deterministic_snapshot_and_completeness_without_mock_fill(self) -> None:
        overpass_payload = {
            "elements": [
                {
                    "type": "node",
                    "id": 101,
                    "lat": 39.9,
                    "lon": 116.4,
                    "tags": {
                        "name": "测试博物馆",
                        "tourism": "museum",
                        "addr:district": "东城区",
                        "addr:street": "测试街",
                        "addr:housenumber": "1号",
                        "opening_hours": "Tu-Su 09:00-17:00",
                        "wikidata": "Q101",
                    },
                },
                {
                    "type": "way",
                    "id": 202,
                    "center": {"lat": 39.91, "lon": 116.41},
                    "tags": {
                        "name": "测试餐厅",
                        "amenity": "restaurant",
                        "cuisine": "chinese",
                    },
                },
                {
                    "type": "node",
                    "id": 303,
                    "lat": 39.92,
                    "lon": 116.42,
                    "tags": {"amenity": "restaurant"},
                },
            ]
        }
        image = {
            "url": "https://upload.wikimedia.org/example.jpg",
            "source_uri": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "author": "Example author",
            "license": "CC-BY-SA-4.0",
            "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": "Example author / CC BY-SA 4.0",
        }

        snapshot = CatalogCollector(max_records=20).build(
            overpass_payload,
            collected_at=COLLECTED_AT,
            images_by_wikidata={"Q101": image},
        )

        self.assertEqual(snapshot["manifest"]["record_count"], 2)
        self.assertEqual(snapshot["manifest"]["source"]["license"], "ODbL-1.0")
        self.assertEqual(
            snapshot["manifest"]["completeness"],
            {
                "address": {"count": 1, "percent": 50.0},
                "opening_hours": {"count": 1, "percent": 50.0},
                "image": {"count": 1, "percent": 50.0},
                "known_or_free_price": {"count": 0, "percent": 0.0},
            },
        )
        museum, restaurant = snapshot["records"]
        self.assertEqual(museum["resource_id"], "osm-node-101")
        self.assertEqual(museum["resource_type"], "activity")
        self.assertEqual(museum["open_hours"]["tue"], "09:00-17:00")
        self.assertEqual(museum["image"]["license"], "CC-BY-SA-4.0")
        self.assertEqual(restaurant["resource_id"], "osm-way-202")
        self.assertEqual(restaurant["price_kind"], "unknown")
        self.assertIsNone(restaurant["avg_price_yuan"])
        self.assertNotIn("dynamic_fields_mock", restaurant)
        self.assertNotIn("estimated_fields", restaurant)
        self.assertNotIn("derived_fields", restaurant)


if __name__ == "__main__":
    unittest.main()
