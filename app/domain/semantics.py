"""Small, evidence-bearing semantic contracts shared by planning seams.

The contracts deliberately separate a finite vocabulary that can trigger a
deterministic policy (``SoftObjective``) from open text used for retrieval
(``SemanticQuery``).  They contain no POI identity, route fact, price or other
authorization to mutate a plan.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.constraints import StopRole


class SoftObjectiveKind(str, Enum):
    """Finite objectives with intentionally stable downstream semantics."""

    LOW_FATIGUE = "low_fatigue"
    SHORTER_TRAVEL = "shorter_travel"
    FEWER_STOPS = "fewer_stops"
    NOVELTY = "novelty"
    QUIET = "quiet"
    CONVERSATION_FRIENDLY = "conversation_friendly"
    ROMANTIC = "romantic"
    FAMILY_FRIENDLY = "family_friendly"
    LOW_SPICE = "low_spice"


class EvidenceRef(BaseModel):
    """A traceable statement that can be cited by semantic decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=1)
    source_type: Literal["user_message", "poi_profile", "fixture_aspect", "provider"]
    source_field: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_ref: str | None = None
    confidence: float = Field(ge=0, le=1)


class SoftObjective(BaseModel):
    """A finite objective with an explicit downstream interpretation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SoftObjectiveKind
    strength: Literal["preferred", "required"] = "preferred"
    target_role: StopRole | None = None
    evidence_refs: tuple[str, ...] = ()


class SemanticQuery(BaseModel):
    """Open user language used to rank semantic POI profiles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    target_role: StopRole | None = None
    evidence_refs: tuple[str, ...] = ()


class SemanticRequest(BaseModel):
    """Unified semantic payload for create and replacement retrieval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence: tuple[EvidenceRef, ...] = ()
    objectives: tuple[SoftObjective, ...] = ()
    queries: tuple[SemanticQuery, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> "SemanticRequest":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("semantic evidence ids must be unique")
        known = set(evidence_ids)
        for references in (
            *(item.evidence_refs for item in self.objectives),
            *(item.evidence_refs for item in self.queries),
        ):
            if not set(references).issubset(known):
                raise ValueError("semantic references must point to known evidence")
        return self

    @property
    def is_empty(self) -> bool:
        return not self.evidence and not self.objectives and not self.queries


def compile_replacement_semantics(
    criteria: tuple[object, ...],
    *,
    target_role: StopRole | None = None,
    evidence: dict[str, str] | None = None,
) -> SemanticRequest:
    """Compile the existing bounded replacement criteria into this contract.

    The helper intentionally accepts the criteria protocol loosely to avoid a
    dependency from the domain contract back into the command implementation.
    ``semantic`` criteria become open queries; a route objective becomes the
    finite ``shorter_travel`` objective.  No candidate is selected here.
    """

    evidence = evidence or {}
    refs: list[EvidenceRef] = []
    for index, (key, value) in enumerate(evidence.items(), start=1):
        refs.append(
            EvidenceRef(
                evidence_id=f"replacement.{index}",
                source_type="user_message",
                source_field=key,
                summary=value,
                confidence=1.0,
            )
        )
    if not refs:
        for index, item in enumerate(criteria, start=1):
            text = getattr(item, "text", None)
            if text:
                refs.append(
                    EvidenceRef(
                        evidence_id=f"replacement.{index}",
                        source_type="user_message",
                        source_field="replacement_criteria",
                        summary=str(text),
                        confidence=1.0,
                    )
                )
    first_ref = refs[0].evidence_id if refs else None
    objectives: list[SoftObjective] = []
    queries: list[SemanticQuery] = []
    for index, item in enumerate(criteria, start=1):
        kind = getattr(item, "kind", None)
        if kind == "route_objective":
            objectives.append(
                SoftObjective(
                    kind="shorter_travel",
                    strength="required",
                    target_role=target_role,
                    evidence_refs=((first_ref,) if first_ref else ()),
                )
            )
        elif kind == "semantic":
            text = str(getattr(item, "text", "")).strip()
            if text:
                ref_id = first_ref or f"replacement.{index}"
                if not refs:
                    refs.append(
                        EvidenceRef(
                            evidence_id=ref_id,
                            source_type="user_message",
                            source_field="replacement_criteria",
                            summary=text,
                            confidence=1.0,
                        )
                    )
                queries.append(
                    SemanticQuery(
                        query_id=f"replacement.query.{index}",
                        text=text,
                        target_role=target_role,
                        evidence_refs=(ref_id,),
                    )
                )
    return SemanticRequest(
        evidence=tuple(refs),
        objectives=tuple(objectives),
        queries=tuple(queries),
    )
