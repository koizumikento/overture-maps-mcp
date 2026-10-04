"""Fixed spatial operations and validated attribute expressions; no user SQL."""

from __future__ import annotations

import base64
import json
import math
import re
from typing import Any

from overture_maps_mcp.models import AttributeFilter, Bounds, Point, Polygon, QueryError
from overture_maps_mcp.query import bbox_filter

MAX_CANDIDATES = 5000
DISTANCE_METHOD = (
    "Local WGS84 azimuthal-equidistant projection in meters. Point distances from the "
    "projection center are geodesic; line/polygon distances use projected straight edges."
)


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def field_expression(path: str, columns: dict[str, str]) -> str:
    if (
        not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\[\])?(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[\])?){0,4}", path
        )
        or path.split(".")[0].removesuffix("[]") not in columns
        or path.split(".")[0].removesuffix("[]") == "geometry"
    ):
        raise QueryError(
            "INVALID_FIELD: select a property from overture_schema; no SQL expressions."
        )

    def descend(parts: list[str], parent: str = "", depth: int = 0) -> str:
        part = parts[0]
        expression = (parent + "." if parent else "") + quote(part.removesuffix("[]"))
        if part.endswith("[]") and len(parts) > 1:
            variable = f"element_{depth}"
            child = descend(parts[1:], variable, depth + 1)
            return f"list_transform({expression}, {variable} -> {child})"
        return descend(parts[1:], expression, depth) if len(parts) > 1 else expression

    return descend(path.split("."))


def compile_filters(filters: list[AttributeFilter], resolve) -> tuple[str, list[Any]]:
    if len(filters) > 20:
        raise QueryError("INVALID_FILTER: use at most 20 AND conditions.")
    clauses, parameters = [], []
    for condition in filters:
        expression, kind = resolve(condition.field)
        op, value = condition.op, condition.value
        is_list = kind.endswith("[]")
        null_test = op in {"is_null", "not_null"} or (value is None and op in {"eq", "ne"})
        if not null_test and kind.startswith(("STRUCT", "MAP", "GEOMETRY", "BLOB")):
            raise QueryError("INVALID_FILTER: select a scalar property or scalar list path.")
        if (
            op in {"contains", "starts_with"}
            and not is_list
            and not kind.startswith(("VARCHAR", "CHAR", "ENUM"))
        ):
            raise QueryError("INVALID_FILTER: text matching requires a text property.")
        if op in {"is_null", "not_null"} or (value is None and op in {"eq", "ne"}):
            clauses.append(f"{expression} IS {'NOT ' if op in {'not_null', 'ne'} else ''}NULL")
        elif op in {"in", "not_in"}:
            if is_list or not isinstance(value, list) or any(v is None for v in value):
                raise QueryError("INVALID_FILTER: in/not_in need scalar non-null values.")
            clauses.append(
                f"{expression} {'NOT ' if op == 'not_in' else ''}IN "
                f"({','.join('?' for _ in value)})"
            )
            parameters.extend(value)
        elif op == "between":
            clauses.append(f"{expression} BETWEEN ? AND ?")
            parameters.extend(value if isinstance(value, list) else [])
        elif op in {"contains_any", "contains_all"}:
            if not is_list or not isinstance(value, list):
                raise QueryError("INVALID_FILTER: contains_any/all require a list-valued field.")
            join = " OR " if op == "contains_any" else " AND "
            clauses.append("(" + join.join(f"list_contains({expression}, ?)" for _ in value) + ")")
            parameters.extend(value)
        elif op == "contains":
            if isinstance(value, list) or value is None:
                raise QueryError("INVALID_FILTER: contains requires one non-null value.")
            clauses.append(
                f"list_contains({expression}, ?)"
                if is_list
                else f"contains(lower(CAST({expression} AS VARCHAR)), lower(?))"
            )
            parameters.append(value)
        elif op == "starts_with":
            if not isinstance(value, str) or is_list:
                raise QueryError("INVALID_FILTER: starts_with requires a string field and value.")
            clauses.append(f"starts_with(lower(CAST({expression} AS VARCHAR)), lower(?))")
            parameters.append(value)
        else:
            if isinstance(value, list) or is_list or value is None:
                raise QueryError("INVALID_FILTER: comparisons require scalar non-null values.")
            symbol = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            clauses.append(f"{expression} {symbol} ?")
            parameters.append(value)
    return " AND ".join(clauses), parameters


def projection(point: Point) -> str:
    return (
        f"+proj=aeqd +lat_0={point.latitude} +lon_0={point.longitude} "
        "+datum=WGS84 +units=m +no_defs"
    )


def distance_expression(point: Point, geometry: str = "geometry") -> str:
    # Only validated finite numeric coordinates enter this trusted CRS string.
    return (
        f"CASE WHEN ST_Intersects({geometry}, "
        f"ST_Point({point.longitude},{point.latitude})) THEN 0.0 ELSE "
        f"ST_Distance(ST_Transform({geometry}, 'EPSG:4326', '{projection(point)}', "
        "always_xy := true), ST_Point(0,0)) END"
    )


def circle_bounds(center: Point, radius_m: float) -> Bounds:
    if not math.isfinite(radius_m) or not 1 <= radius_m <= 25000:
        raise QueryError("INVALID_RADIUS: use 1 to 25000 meters.")
    delta = radius_m / 6_330_000
    latitude = math.radians(center.latitude)
    if abs(latitude) + delta >= math.pi / 2:
        raise QueryError("INVALID_AREA: circle crosses a pole; choose a smaller radius.")
    dlat = math.degrees(delta)
    dlon = math.degrees(math.asin(math.sin(delta) / math.cos(latitude)))
    try:
        return Bounds(
            west=center.longitude - dlon,
            east=center.longitude + dlon,
            south=center.latitude - dlat,
            north=center.latitude + dlat,
        )
    except ValueError as exc:
        raise QueryError(
            "INVALID_AREA: circle exceeds the area limit or crosses the antimeridian."
        ) from exc


def spatial_scope(
    bounds: Bounds | None,
    center: Point | None = None,
    radius_m: float | None = None,
    polygon: Polygon | None = None,
) -> tuple[Bounds, str, list[Any], dict[str, Any]]:
    if polygon is not None:
        if bounds is not None or radius_m is not None:
            raise QueryError("INVALID_AREA: choose exactly one of bounds, circle or polygon.")
        selected = polygon.extent()
        clause, parameters = bbox_filter(selected)
        # Replace the rectangle intersection, keeping bbox pruning.
        clause = (
            clause.split(" AND ST_Intersects")[0]
            + " AND ST_Intersects(geometry, ST_GeomFromGeoJSON(?))"
        )
        return (
            selected,
            clause,
            [*parameters[:4], polygon.model_dump_json()],
            {"polygon": polygon.model_dump()},
        )
    if radius_m is not None:
        if center is None or bounds is not None:
            raise QueryError("INVALID_AREA: circle needs center/radius_m and no bounds.")
        selected = circle_bounds(center, radius_m)
        clause, parameters = bbox_filter(selected)
        clause += f" AND {distance_expression(center)} <= ?"
        return (
            selected,
            clause,
            [*parameters, radius_m],
            {"center": center.model_dump(), "radius_m": radius_m},
        )
    if bounds is None:
        raise QueryError("INVALID_AREA: supply bounds, center/radius_m or a GeoJSON polygon.")
    clause, parameters = bbox_filter(bounds)
    return bounds, clause, parameters, {"bounds": bounds.model_dump()}


def metric_expression(field: str, geometry: str = "geometry", center: Point | None = None) -> str:
    if field == "@area_m2":
        return f"ST_Area_Spheroid(ST_FlipCoordinates({geometry}))"
    if field == "@length_m":
        return f"ST_Length_Spheroid(ST_FlipCoordinates({geometry}))"
    if field == "@distance_m" and center is not None:
        return distance_expression(center, geometry)
    raise QueryError("INVALID_METRIC: use @area_m2, @length_m or @distance_m (with center).")


def page_encode(values: list[Any], key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([values, key], allow_nan=False).encode()).decode()


def page_decode(cursor: str | None, key: str, size: int) -> list[Any] | None:
    if cursor is None:
        return None
    try:
        if len(cursor) > 2048:
            raise ValueError
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        values = payload[0]
        if payload[1] != key or not isinstance(values, list) or len(values) != size:
            raise ValueError
        if any(not isinstance(v, str | int | float) or isinstance(v, bool) for v in values):
            raise ValueError
        if any(isinstance(v, float) and not math.isfinite(v) for v in values):
            raise ValueError
        return values
    except (ValueError, TypeError, IndexError, KeyError) as exc:
        raise QueryError(
            "INVALID_CURSOR: retain release, scope, filters, fields and ordering; "
            "restart if changed."
        ) from exc
