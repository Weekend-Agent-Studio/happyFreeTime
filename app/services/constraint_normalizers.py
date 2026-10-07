"""Deterministic normalization for bounded request constraint values."""

from __future__ import annotations

from app.domain.constraints import ConstraintSource, ConstraintValue


class DistanceNormalizer:
    """Normalize the finite distance vocabulary without deciding its scope."""

    DEFAULT_DISTANCE_KM = 8.0
    _ALIASES: dict[str, tuple[float, str]] = {
        "步行可达": (2.0, "distance.walkable.v1"),
        "附近": (5.0, "distance.nearby.v1"),
        "别太远": (DEFAULT_DISTANCE_KM, "distance.not_far.beijing.v1"),
    }

    @classmethod
    def normalize(
        cls,
        explicit_km: float | None,
        raw_text: str | None,
    ) -> ConstraintValue[float] | None:
        if explicit_km is not None:
            return ConstraintValue[float](
                value=explicit_km,
                source=ConstraintSource.USER_EXPLICIT,
                raw_text=raw_text,
            )
        if raw_text:
            rule = cls._ALIASES.get(raw_text.strip())
            if rule is None:
                return None
            value, rule_id = rule
            return ConstraintValue[float](
                value=value,
                source=ConstraintSource.USER_INFERRED,
                raw_text=raw_text,
                rule_id=rule_id,
            )
        return ConstraintValue[float](
            value=cls.DEFAULT_DISTANCE_KM,
            source=ConstraintSource.DEFAULT_RULE,
            rule_id="distance.default.beijing.v1",
        )
