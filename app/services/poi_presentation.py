"""Presentation seam for looking up details of the POIs a plan actually uses."""

from __future__ import annotations

from typing import Protocol

from app.domain.presentation import PoiPresentation


class PoiPresentationProvider(Protocol):
    def present_many(self, resource_ids: list[str]) -> list[PoiPresentation]:
        ...


class EmptyPoiPresentationProvider:
    """Safe baseline for SnapshotCatalog and focused planning tests."""

    def present_many(self, resource_ids: list[str]) -> list[PoiPresentation]:
        return []
