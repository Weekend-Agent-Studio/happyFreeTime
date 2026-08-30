"""POI display contract kept outside the planning candidate model.

The planner only needs feasibility and scoring inputs.  Rich commercial-looking
copy, galleries and labels belong to this contract, keyed by the same
``resource_id`` used by a plan stop.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PoiGalleryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=1)
    alt: str = Field(min_length=1)
    kind: Literal["source", "illustrative"]
    attribution: str | None = None
    license: str | None = None


class PoiPresentation(BaseModel):
    """Stable, presentation-only details for one planned POI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str
    name: str
    category_label: str
    business_area: str
    address: str
    description: str
    scene_tags: list[str] = Field(default_factory=list)
    facility_tags: list[str] = Field(default_factory=list)
    indoor: bool
    weather_suitability: str
    child_suitability: str
    reference_avg_price: int | None = Field(default=None, ge=0)
    demo_rating: float = Field(ge=0, le=5)
    demo_review_count: int = Field(ge=0)
    opening_hours_display: str
    opening_status: str
    suggested_duration_minutes: int = Field(gt=0)
    reservation_requirement: str
    queue_profile: str
    risk_tips: list[str] = Field(default_factory=list)
    booking_mode: str
    gallery: list[PoiGalleryItem] = Field(default_factory=list)
    data_notice: str
