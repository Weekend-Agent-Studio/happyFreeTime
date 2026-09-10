import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.domain.semantics import (
    POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
    PoiSemanticAspect,
    PoiSemanticProfile,
)
from app.services.catalog import SnapshotCatalog
from app.services.poi_semantic_profiles import (
    SemanticProfileDataError,
    PoiSemanticProfileAssembler,
    load_poi_semantic_profiles,
    profile_retrieval_text,
    semantic_profile_source_hash,
)
from tests.test_native_planning import candidate
from app.domain.catalog import ResourceType


class SemanticProfileTest(unittest.TestCase):
    def test_repository_profiles_are_versioned_and_match_catalog_identities(self) -> None:
        profiles = load_poi_semantic_profiles()
        candidates = SnapshotCatalog().load_candidates()
        candidate_by_id = {item.resource_id: item for item in candidates}

        self.assertEqual(len(profiles), 30)
        self.assertTrue(set(profiles).issubset(candidate_by_id))
        self.assertTrue(
            all(
                aspect.source_type == "fixture"
                for profile in profiles.values()
                for aspect in profile.aspects
            )
        )
        assembled = PoiSemanticProfileAssembler(profiles).assemble_many(candidates)
        self.assertEqual(len(assembled), len(candidates))
        self.assertEqual(
            profile_retrieval_text(assembled[0]),
            profile_retrieval_text(assembled[0]),
        )

    def test_profile_rejects_duplicate_aspects_and_dynamic_fields(self) -> None:
        aspect = {
            "aspect_id": "fixture.quiet",
            "kind": "quiet",
            "text": "安静",
            "source_type": "fixture",
            "source_ref": "fixture://quiet",
            "confidence": 0.8,
        }
        with self.assertRaises(ValidationError):
            PoiSemanticProfile.model_validate(
                {
                    "schema_version": POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
                    "resource_id": "poi-1",
                    "name": "地点",
                    "summary": "摘要",
                    "aspects": [aspect, aspect],
                }
            )
        with self.assertRaises(ValidationError):
            PoiSemanticProfile.model_validate(
                {
                    "schema_version": POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
                    "resource_id": "poi-1",
                    "name": "地点",
                    "summary": "摘要",
                    "weather": "晴天",
                }
            )

    def test_loader_rejects_duplicate_resource_ids(self) -> None:
        profile = PoiSemanticProfile(
            schema_version=POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
            resource_id="poi-1",
            name="地点",
            summary="摘要",
        ).model_dump(mode="json")
        payload = {
            "schema_version": POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
            "profiles": [profile, profile],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(SemanticProfileDataError):
                load_poi_semantic_profiles(path)

    def test_loader_rejects_dynamic_top_level_fields(self) -> None:
        payload = {
            "schema_version": POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
            "profiles": [],
            "weather": "晴天",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(SemanticProfileDataError):
                load_poi_semantic_profiles(path)

    def test_assembler_rejects_name_mismatch_and_builds_catalog_fallback(self) -> None:
        candidate_item = candidate(
            "poi-1",
            ResourceType.ACTIVITY,
            "目录名称",
            ["展览"],
        )
        wrong = PoiSemanticProfile(
            schema_version=POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
            resource_id="poi-1",
            name="另一名称",
            summary="摘要",
        )
        with self.assertRaises(SemanticProfileDataError):
            PoiSemanticProfileAssembler([wrong]).assemble(candidate_item)

        fallback = PoiSemanticProfileAssembler().assemble(candidate_item)
        self.assertEqual(fallback.resource_id, candidate_item.resource_id)
        self.assertIn("目录名称", fallback.summary)
        self.assertTrue(fallback.aspects)
        self.assertEqual(
            semantic_profile_source_hash([fallback]),
            semantic_profile_source_hash([fallback]),
        )


if __name__ == "__main__":
    unittest.main()
