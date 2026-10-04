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


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class Polygon(BaseModel):
    """GeoJSON Polygon or MultiPolygon, including holes, in longitude/latitude order."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type: Literal["Polygon", "MultiPolygon"] = "Polygon"
    coordinates: list[Any] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_rings(self) -> Polygon:
        polygons = [self.coordinates] if self.type == "Polygon" else self.coordinates
        points = []
        for polygon in polygons:
            if not isinstance(polygon, list) or not 1 <= len(polygon) <= 20:
                raise ValueError("Use 1 to 20 rings per polygon.")
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                    raise ValueError("Each ring needs at least 4 positions and must be closed.")
                for position in ring:
                    if not isinstance(position, list) or len(position) != 2:
                        raise ValueError("Positions must be [longitude, latitude].")
                    lon, lat = position
                    if (
                        isinstance(lon, bool)
                        or isinstance(lat, bool)
                        or not isinstance(lon, int | float)
                        or not isinstance(lat, int | float)
                        or not math.isfinite(lon)
                        or not math.isfinite(lat)
                        or not -180 <= lon <= 180
                        or not -90 <= lat <= 90
                    ):
                        raise ValueError("Use finite WGS84 coordinates.")
                    points.append(position)
        if len(points) > 2000:
            raise ValueError("Use at most 2000 polygon positions.")
        self.extent()
        return self

    def extent(self) -> Bounds:
        polygons = [self.coordinates] if self.type == "Polygon" else self.coordinates
        points = [p for polygon in polygons for ring in polygon for p in ring]
        return Bounds(
            west=min(p[0] for p in points),
            east=max(p[0] for p in points),
            south=min(p[1] for p in points),
            north=max(p[1] for p in points),
        )


Scalar = str | int | float | bool | None


class AttributeFilter(BaseModel):
    """Column/struct path; [] traverses a list, e.g. sources[].dataset."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    field: str = Field(min_length=1, max_length=160)
    op: Literal[
        "eq",
        "ne",
        "in",
        "not_in",
        "gt",
        "gte",
        "lt",
        "lte",
        "between",
        "contains",
        "starts_with",
        "is_null",
        "not_null",
        "contains_any",
        "contains_all",
    ]
    value: Scalar | list[Scalar] = None

    @model_validator(mode="after")
    def check_value(self) -> AttributeFilter:
        values = self.value if isinstance(self.value, list) else [self.value]
        if len(values) > 50 or any(isinstance(v, str) and len(v) > 512 for v in values):
            raise ValueError("Filter values are limited to 50 items / 512 characters each.")
        if self.op in {"in", "not_in", "contains_any", "contains_all", "between"}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("This operator requires a nonempty value list.")
        if self.op == "between" and len(values) != 2:
            raise ValueError("between requires [minimum, maximum].")
        return self


class Aggregation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=160)
    op: Literal["sum", "avg", "min", "max", "count", "count_distinct"]
    label: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Theme
    feature_type: str
    filters: list[AttributeFilter] = Field(default_factory=list, max_length=20)
    fields: list[str] | None = Field(default=None, max_length=64)
