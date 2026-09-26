"""Versioned POI semantic profiles and deterministic profile assembly.

The catalog remains the source of identity and hard facts.  This module only
adds stable descriptive material that can be used by a semantic retriever.  A
profile is never allowed to carry a route, weather, opening, availability or
inventory observation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from app.domain.catalog import StopCandidate
from app.domain.semantics import (
    POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
    PoiSemanticAspect,
    PoiSemanticProfile,
    SoftObjectiveKind,
)


_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = _ROOT / "data" / "retrieval" / "v1" / "poi_semantic_profiles.json"
_RETRIEVAL_NOTICE = "；仅用于离线语义召回，不代表实时营业、路线或真实用户评价。"


class SemanticProfileDataError(ValueError):
    """Profile data is missing, malformed or inconsistent with the catalog."""


def load_poi_semantic_profiles(
    path: Path | None = None,
) -> dict[str, PoiSemanticProfile]:
    """Load and validate the versioned profile collection from JSON."""

    profile_path = path or DEFAULT_PROFILE_PATH
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SemanticProfileDataError(
            f"cannot read POI semantic profiles {profile_path}: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise SemanticProfileDataError(
            f"{profile_path}: profile document must be a JSON object"
        )
    unknown_fields = set(payload) - {"schema_version", "profiles"}
    if unknown_fields:
        raise SemanticProfileDataError(
            f"{profile_path}: unsupported top-level fields: "
            + ", ".join(sorted(unknown_fields))
        )
    if payload.get("schema_version") != POI_SEMANTIC_PROFILE_SCHEMA_VERSION:
        raise SemanticProfileDataError(
            f"{profile_path}: unsupported profile schema_version"
        )
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise SemanticProfileDataError(
            f"{profile_path}: profiles must be a JSON array"
        )

    profiles: dict[str, PoiSemanticProfile] = {}
    for index, raw_profile in enumerate(raw_profiles, start=1):
        try:
            profile = PoiSemanticProfile.model_validate(raw_profile)
        except (TypeError, ValidationError) as exc:
            raise SemanticProfileDataError(
                f"{profile_path}: profile {index} is invalid: {exc}"
            ) from exc
        if profile.resource_id in profiles:
            raise SemanticProfileDataError(
                f"{profile_path}: duplicate resource_id {profile.resource_id!r}"
            )
        profiles[profile.resource_id] = profile
    return profiles


def semantic_profile_source_hash(
    profiles: Mapping[str, PoiSemanticProfile] | Iterable[PoiSemanticProfile],
) -> str:
    """Return a stable SHA-256 hash for the semantic profile source."""

    if isinstance(profiles, Mapping):
        values = profiles.values()
    else:
        values = profiles
    canonical = [
        profile.model_dump(mode="json")
        for profile in sorted(values, key=lambda item: item.resource_id)
    ]
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PoiSemanticProfileAssembler:
    """Merge curated semantic data with stable catalog identity fields."""

    def __init__(
        self,
        profiles: Mapping[str, PoiSemanticProfile]
        | Sequence[PoiSemanticProfile]
        | None = None,
        *,
        profile_path: Path | None = None,
    ) -> None:
        if profiles is not None and profile_path is not None:
            raise ValueError("provide profiles or profile_path, not both")
        if profile_path is not None:
            loaded = load_poi_semantic_profiles(profile_path)
        elif profiles is None:
            loaded = (
                load_poi_semantic_profiles()
                if DEFAULT_PROFILE_PATH.exists()
                else {}
            )
        elif isinstance(profiles, Mapping):
            loaded = dict(profiles)
        else:
            loaded = {}
            for profile in profiles:
                if profile.resource_id in loaded:
                    raise SemanticProfileDataError(
                        f"duplicate resource_id {profile.resource_id!r}"
                    )
                loaded[profile.resource_id] = profile

        for key, profile in loaded.items():
            if key != profile.resource_id:
                raise SemanticProfileDataError(
                    "profile mapping key must equal profile.resource_id"
                )
        self._profiles = loaded

    @property
    def profiles(self) -> dict[str, PoiSemanticProfile]:
        """Return a defensive mapping for index builders and diagnostics."""

        return dict(self._profiles)

    @property
    def source_hash(self) -> str:
        return semantic_profile_source_hash(self._profiles)

    def assemble(self, candidate: StopCandidate) -> PoiSemanticProfile:
        """Build one deterministic profile for a catalog candidate."""

        curated = self._profiles.get(candidate.resource_id)
        catalog_aspects = _catalog_aspects(candidate)
        catalog_context = _catalog_context(candidate)
        if curated is None:
            return PoiSemanticProfile(
                schema_version=POI_SEMANTIC_PROFILE_SCHEMA_VERSION,
                resource_id=candidate.resource_id,
                name=candidate.name,
                summary=catalog_context,
                aspects=tuple(catalog_aspects),
            )

        if curated.name != candidate.name:
            raise SemanticProfileDataError(
                f"profile name mismatch for {candidate.resource_id}: "
                f"{curated.name!r} != {candidate.name!r}"
            )
        curated_ids = {aspect.aspect_id for aspect in curated.aspects}
        merged_aspects = [
            *curated.aspects,
            *(aspect for aspect in catalog_aspects if aspect.aspect_id not in curated_ids),
        ]
        summary = f"{_semantic_summary(curated.summary)}；{catalog_context}"
        return curated.model_copy(
            update={
                "summary": summary,
                "aspects": tuple(merged_aspects),
            }
        )

    def assemble_many(
        self,
        candidates: Sequence[StopCandidate],
    ) -> tuple[PoiSemanticProfile, ...]:
        """Validate profile identities and assemble candidates in input order."""

        candidate_ids = {candidate.resource_id for candidate in candidates}
        unknown_ids = sorted(set(self._profiles) - candidate_ids)
        if unknown_ids:
            raise SemanticProfileDataError(
                "profiles reference resources absent from catalog: "
                + ", ".join(unknown_ids)
            )
        return tuple(self.assemble(candidate) for candidate in candidates)

    def validate_catalog(self, candidates: Sequence[StopCandidate]) -> None:
        """Raise when curated identities cannot be reconciled with a catalog."""

        self.assemble_many(candidates)


def profile_retrieval_text(profile: PoiSemanticProfile) -> str:
    """Create stable text for passage embedding without dynamic observations."""

    parts = [profile.name, *profile.aliases, _semantic_summary(profile.summary)]
    parts.extend(aspect.text for aspect in profile.aspects)
    return "\n".join(part.strip() for part in parts if part.strip())


def _semantic_summary(value: str) -> str:
    """Keep source notices out of the retrieval text while retaining them in JSON."""

    return value.strip().removesuffix(_RETRIEVAL_NOTICE).strip()


def _catalog_context(candidate: StopCandidate) -> str:
    parts = [candidate.name]
    if candidate.category_tags:
        parts.append(f"类别：{'、'.join(dict.fromkeys(candidate.category_tags))}")
    if candidate.district:
        parts.append(f"区域：{candidate.district}")
    if candidate.address:
        parts.append(f"地址：{candidate.address}")
    return "；".join(parts)


def _catalog_aspects(candidate: StopCandidate) -> list[PoiSemanticAspect]:
    aspects: list[PoiSemanticAspect] = []
    updated_at = (
        candidate.source.last_verified_at.date()
        if candidate.source.last_verified_at is not None
        else None
    )
    for source_field, values in (
        ("category_tags", candidate.category_tags),
        ("preference_tags", candidate.preference_tags),
        ("diet_tags", candidate.diet_tags),
        ("scene_tags", candidate.scene_tags),
    ):
        for index, value in enumerate(dict.fromkeys(values), start=1):
            text = value.strip()
            if not text:
                continue
            aspects.append(
                PoiSemanticAspect(
                    aspect_id=f"catalog.{source_field}.{index}",
                    kind=_objective_for_text(text),
                    text=text,
                    source_type="catalog",
                    source_ref=candidate.source.source_uri,
                    confidence=0.65,
                    updated_at=updated_at,
                )
            )
    return aspects


def _objective_for_text(text: str) -> SoftObjectiveKind | None:
    normalized = text.casefold()
    mapping: tuple[tuple[SoftObjectiveKind, tuple[str, ...]], ...] = (
        (SoftObjectiveKind.LOW_FATIGUE, ("轻松", "不累", "休闲", "慢慢逛")),
        (SoftObjectiveKind.SHORTER_TRAVEL, ("近", "附近", "少走")),
        (SoftObjectiveKind.NOVELTY, ("新鲜", "新奇", "探索", "展览")),
        (SoftObjectiveKind.QUIET, ("安静", "清静")),
        (SoftObjectiveKind.CONVERSATION_FRIENDLY, ("聊天", "适合聊天")),
        (SoftObjectiveKind.ROMANTIC, ("约会", "浪漫")),
        (SoftObjectiveKind.FAMILY_FRIENDLY, ("亲子", "家庭", "儿童")),
        (SoftObjectiveKind.LOW_SPICE, ("少辣", "不辣", "清淡", "少油")),
    )
    for kind, terms in mapping:
        if any(term.casefold() in normalized for term in terms):
            return kind
    return None
