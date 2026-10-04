from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Theme = Literal["addresses", "base", "buildings", "divisions", "places", "transportation"]
TYPES: dict[str, tuple[str, ...]] = {
    "addresses": ("address",),
    "base": ("bathymetry", "infrastructure", "land", "land_cover", "land_use", "water"),
    "buildings": ("building", "building_part"),
    "divisions": ("division", "division_area", "division_boundary"),
    "places": ("place",),
    "transportation": ("segment", "connector"),
}
LICENSES = {
    "addresses": "Source-specific permissive licenses; inspect sources and attribution page",
    "places": "Source-specific CDLA-Permissive-2.0 / Apache-2.0 / CC0-1.0",
    **dict.fromkeys(("base", "buildings", "divisions", "transportation"), "ODbL-1.0"),
}
ATTRIBUTION_URL = "https://docs.overturemaps.org/attribution/"


class Bounds(BaseModel):
    """WGS84 rectangle. Antimeridian crossing is intentionally not supported."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def check_area(self) -> Bounds:
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("Require west < east and south < north; split antimeridian areas.")
        width, height = self.east - self.west, self.north - self.south
        area = width * height * 111.32**2 * math.cos(math.radians((self.north + self.south) / 2))
        if width > 1 or height > 1 or area > 2500:
            raise ValueError("Area too large: use spans <= 1 degree and area <= 2500 km².")
        return self


class Response(BaseModel):
    """Bounded, provenance-bearing structured result shared by the tools."""

    release: str
    theme: str | None = None
    feature_type: str | None = None
    source: str
    license: str
    attribution_url: str = ATTRIBUTION_URL
    scope: Bounds | None = None
    data: dict[str, Any]
    next_cursor: str | None = None
    warnings: list[str] = Field(default_factory=list)


class QueryError(Exception):
    """User-actionable failure; never return database exceptions to the agent."""
