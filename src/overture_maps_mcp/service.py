from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from overture_maps_mcp.analysis import (
    DISTANCE_METHOD,
    MAX_CANDIDATES,
    compile_filters,
    distance_expression,
    field_expression,
    metric_expression,
    page_decode,
    page_encode,
    projection,
    quote,
    spatial_scope,
)
from overture_maps_mcp.catalog import CATALOG_URL, Catalog, get_catalog
from overture_maps_mcp.models import (
    LICENSES,
    TYPES,
    Aggregation,
    AttributeFilter,
    Bounds,
    Dataset,
    Point,
    Polygon,
    QueryError,
    Response,
    Theme,
)
from overture_maps_mcp.query import Query, bounded, cursor_key, execute

REGISTRY = "s3://overturemaps-us-west-2/registry/*.parquet"


@dataclass
class Plan:
    release: str
    path: str
    files: list[str]
    columns: dict[str, str]
    bounds: Bounds
    clause: str
    parameters: list[Any]
    area: dict[str, Any]
    filters: list[AttributeFilter]


class Service:
    def __init__(self, catalog: Catalog | None = None, query: Query = execute) -> None:
        self.catalog = catalog or get_catalog()
        self.query = query
        self._columns: dict[str, dict[str, str]] = {}
        self._field_types: dict[tuple[str, str], str] = {}
        self._polygons: set[str] = set()

    def dataset(self, theme: Theme, feature_type: str, release: str | None) -> tuple[str, str]:
        if theme not in TYPES or feature_type not in TYPES[theme]:
            raise QueryError("INVALID_TYPE: call overture_catalog for valid theme/type pairs.")
        selected, _ = self.catalog.resolve(release)
        return selected, (
            f"s3://overturemaps-us-west-2/release/{selected}/"
            f"theme={theme}/type={feature_type}/*.parquet"
        )

    def response(
        self,
        release: str,
        theme: Theme,
        feature_type: str,
        path: str,
        bounds: Bounds | None,
        data: dict[str, Any],
        cursor: str | None = None,
    ) -> Response:
        warnings = ["Counts describe the dataset in this scope, not real-world completeness."]
        if theme == "addresses":
            warnings.append(
                "Addresses is Alpha; IDs are not covered by GERS stability commitments."
            )
        if theme == "base" or feature_type == "building_part":
            warnings.append("These feature IDs do not carry a GERS stability commitment.")
        if theme == "places":
            warnings.append(
                "Confidence measures existence, not location accuracy or current opening."
            )
        return Response(
            release=release,
            theme=theme,
            feature_type=feature_type,
            source=path,
            license=LICENSES[theme],
            scope=bounds,
            data=bounded(data),
            next_cursor=cursor,
            warnings=warnings,
        )

    def info(self) -> Response:
        selected, releases = self.catalog.resolve()
        return Response(
            release=selected,
            source=CATALOG_URL,
            license="Theme/source-specific",
            data={
                "available_releases": releases,
                "themes": TYPES,
                "licenses": LICENSES,
                "max_area_km2": 2500,
                "max_span_degrees": 1,
                "max_page_size": 50,
                "query_timeout_seconds": 30,
                "max_radius_m": 25000,
                "join_compare_max_candidates_per_dataset": MAX_CANDIDATES,
                "metrics": ["@area_m2", "@length_m", "@distance_m"],
                "filter_operators": AttributeFilter.model_json_schema()["properties"]["op"]["enum"],
            },
            warnings=[
                "Pin the returned release for workflows and pagination.",
                "Public releases are retained for up to 60 days.",
            ],
        )

    def columns(self, path: str, files: list[str]) -> dict[str, str]:
        if path not in self._columns:
            rows = self.query("DESCRIBE SELECT * FROM read_parquet(?)", [files])
            self._columns[path] = {r["column_name"]: r["column_type"] for r in rows}
        return self._columns[path]

    def schema(self, theme: Theme, feature_type: str, release: str | None) -> Response:
        selected, path = self.dataset(theme, feature_type, release)
        files = self.catalog.files(selected, theme, feature_type)
        return self.response(
            selected,
            theme,
            feature_type,
            path,
            None,
            {
                "columns": self.columns(path, files),
                "field_paths": "column.struct_field; list[].struct_field",
                "virtual_metrics": ["@area_m2", "@length_m", "@distance_m"],
            },
        )

    def field(
        self, path: str, files: list[str], columns: dict[str, str], name: str
    ) -> tuple[str, str]:
        expression = field_expression(name, columns)
        if name in columns:
            return expression, columns[name]
        key = path, name
        if key not in self._field_types:
            try:
                rows = self.query(
                    f"DESCRIBE SELECT {expression} AS value FROM read_parquet(?)", [files]
                )
                self._field_types[key] = rows[0]["column_type"]
            except QueryError as exc:
                raise QueryError(
                    "INVALID_FIELD: nested property is unavailable; inspect overture_schema."
                ) from exc
        return expression, self._field_types[key]

    def prepare(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds | None,
        release: str | None,
        filters: list[AttributeFilter] | None = None,
        name: str | None = None,
        category: str | None = None,
        feature_class: str | None = None,
        min_confidence: float | None = None,
        center: Point | None = None,
        radius_m: float | None = None,
        polygon: Polygon | None = None,
    ) -> Plan:
        selected, path = self.dataset(theme, feature_type, release)
        area_bounds, clause, params, area = spatial_scope(bounds, center, radius_m, polygon)
        if polygon is not None and polygon.model_dump_json() not in self._polygons:
            valid = self.query(
                "SELECT ST_IsValid(ST_GeomFromGeoJSON(?)) AS valid", [polygon.model_dump_json()]
            )
            if not valid[0]["valid"]:
                raise QueryError("INVALID_POLYGON: fix self-intersections or invalid holes/rings.")
            if len(self._polygons) >= 128:
                self._polygons.clear()
            self._polygons.add(polygon.model_dump_json())
        schema_files = self.catalog.files(selected, theme, feature_type)
        columns = self.columns(path, schema_files)
        conditions = list(filters or [])
        for value, column, field, op in (
            (name, "names", "names.primary", "contains"),
            (category, "taxonomy", "taxonomy.primary", "eq"),
            (feature_class, "class", "class", "eq"),
            (min_confidence, "confidence", "confidence", "gte"),
        ):
            if value is not None:
                if column not in columns:
                    raise QueryError(f"UNSUPPORTED_FILTER: {column} is unavailable in this type.")
                conditions.append(AttributeFilter(field=field, op=op, value=value))
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise QueryError("INVALID_CONFIDENCE: use a value between 0 and 1.")
        extra, values = compile_filters(
            conditions, lambda f: self.field(path, schema_files, columns, f)
        )
        if extra:
            clause += " AND " + extra
            params.extend(values)
        files = self.catalog.files(selected, theme, feature_type, area_bounds)
        return Plan(
            selected, path, files, columns, area_bounds, clause, [files, *params], area, conditions
        )

    def properties(self, plan: Plan, fields: list[str] | None) -> list[tuple[str, str]]:
        if fields is not None and (
            not fields or len(fields) > 64 or len(set(fields)) != len(fields)
        ):
            raise QueryError(
                "INVALID_FIELDS: choose 1 to 64 unique property paths or omit fields for all."
            )
        selected = (
            list(fields) if fields is not None else [c for c in plan.columns if c != "geometry"]
        )
        for mandatory in ("id", "sources"):
            if mandatory in plan.columns and mandatory not in selected:
                selected.append(mandatory)
        files = self.catalog.files(plan.release, self._theme(plan), self._type(plan))
        return [(name, self.field(plan.path, files, plan.columns, name)[0]) for name in selected]

    @staticmethod
    def _theme(plan: Plan) -> Theme:
        match = re.search(r"theme=([^/]+)", plan.path)
        assert match is not None
        return cast(Theme, match[1])

    @staticmethod
    def _type(plan: Plan) -> str:
        match = re.search(r"type=([^/]+)", plan.path)
        assert match is not None
        return match[1]

    @staticmethod
    def fingerprint(plan: Plan) -> dict[str, Any]:
        return {"area": plan.area, "filters": [f.model_dump() for f in plan.filters]}

    def search(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds | None = None,
        release: str | None = None,
        name: str | None = None,
        category: str | None = None,
        feature_class: str | None = None,
        min_confidence: float | None = None,
        limit: int = 20,
        cursor: str | None = None,
        include_geometry: bool = False,
        identifier: str | None = None,
        filters: list[AttributeFilter] | None = None,
        fields: list[str] | None = None,
        center: Point | None = None,
        radius_m: float | None = None,
        polygon: Polygon | None = None,
        sort_by: Literal["id", "distance"] = "id",
        include_metrics: bool = False,
        output_format: Literal["features", "geojson"] = "features",
    ) -> Response:
        if not 1 <= limit <= 50:
            raise QueryError("INVALID_LIMIT: use 1 to 50.")
        if sort_by == "distance" and center is None:
            raise QueryError("INVALID_ORDER: distance ordering requires center.")
        if output_format == "geojson":
            include_geometry = True
        plan = self.prepare(
            theme,
            feature_type,
            bounds,
            release,
            filters,
            name,
            category,
            feature_class,
            min_confidence,
            center,
            radius_m,
            polygon,
        )
        if identifier is not None:
            identifier = self.identifier(identifier)
            plan.clause += " AND id = ?"
            plan.parameters.append(identifier)
        key = cursor_key(
            plan.release,
            theme,
            feature_type,
            self.fingerprint(plan)
            | {
                "fields": fields,
                "geometry": include_geometry,
                "center": center.model_dump() if center else None,
                "sort": sort_by,
                "metrics": include_metrics,
                "format": output_format,
                "id": identifier,
            },
        )
        after = page_decode(cursor, key, 2 if sort_by == "distance" else 1)
        projections = [
            f"{expression} AS {quote(field)}" for field, expression in self.properties(plan, fields)
        ]
        if include_geometry:
            projections.append("ST_AsGeoJSON(geometry) AS geometry")
        if center is not None:
            projections.append(distance_expression(center) + " AS __distance")
        if include_metrics:
            projections.extend(
                [
                    metric_expression("@area_m2") + " AS __area",
                    metric_expression("@length_m") + " AS __length",
                ]
            )
        order = "__distance, id" if sort_by == "distance" else "id"
        continuation, extra = "", []
        if after:
            continuation = (
                "WHERE (__distance, id) > (?, ?)" if sort_by == "distance" else "WHERE id > ?"
            )
            extra = after
        rows = (
            self.query(
                f"WITH candidates AS (SELECT {', '.join(projections)} FROM read_parquet(?) "
                f"WHERE {plan.clause}) SELECT * FROM candidates {continuation} "
                f"ORDER BY {order} LIMIT ?",
                [*plan.parameters, *extra, limit + 1],
            )
            if plan.files
            else []
        )
        more, rows = len(rows) > limit, rows[:limit]
        token = None
        if more:
            last = rows[-1]
            values = (
                [last["__distance"], str(last["id"])]
                if sort_by == "distance"
                else [str(last["id"])]
            )
            token = page_encode(values, key)
        for row in rows:
            if include_geometry and row.get("geometry") is not None:
                row["geometry"] = json.loads(row["geometry"])
            if center is not None:
                row["distance_m"] = row.pop("__distance")
            if include_metrics:
                row["metrics"] = {
                    "area_m2": row.pop("__area"),
                    "length_m": row.pop("__length"),
                    "scope": "whole_feature",
                }
        data: dict[str, Any] = {
            "features": rows,
            "returned": len(rows),
            "geometry_included": include_geometry,
            "query": self.fingerprint(plan),
            "sort_by": sort_by,
        }
        if center is not None:
            data["distance_method"] = DISTANCE_METHOD
        if output_format == "geojson":
            data["feature_collection"] = {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": row["id"],
                        "geometry": row.get("geometry"),
                        "properties": {k: v for k, v in row.items() if k not in {"id", "geometry"}},
                    }
                    for row in rows
                ],
            }
            del data["features"]
        return self.response(plan.release, theme, feature_type, plan.path, plan.bounds, data, token)

    def summarize(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds | None = None,
        group_by: str | list[str] | None = None,
        release: str | None = None,
        filters: list[AttributeFilter] | None = None,
        name: str | None = None,
        category: str | None = None,
        feature_class: str | None = None,
        min_confidence: float | None = None,
        center: Point | None = None,
        radius_m: float | None = None,
        polygon: Polygon | None = None,
        aggregations: list[Aggregation] | None = None,
        clip_geometry: bool = True,
    ) -> Response:
        plan = self.prepare(
            theme,
            feature_type,
            bounds,
            release,
            filters,
            name,
            category,
            feature_class,
            min_confidence,
            center,
            radius_m,
            polygon,
        )
        groups = [group_by] if isinstance(group_by, str) else group_by or []
        if len(groups) > 3 or len(set(groups)) != len(groups):
            raise QueryError("INVALID_GROUP: choose at most 3 unique scalar property paths.")
        files = self.catalog.files(plan.release, theme, feature_type)
        expressions = []
        for field in groups:
            try:
                expression, kind = self.field(plan.path, files, plan.columns, field)
            except QueryError as exc:
                raise QueryError(
                    "INVALID_GROUP: choose a scalar property from overture_schema."
                ) from exc
            if kind.endswith("[]") or kind.startswith(("STRUCT", "MAP", "GEOMETRY")):
                raise QueryError(
                    "INVALID_GROUP: group by scalar properties, not nested objects or lists."
                )
            expressions.append(expression)
        aggregates = aggregations or []
        if len(aggregates) > 10 or len({a.label for a in aggregates}) != len(aggregates):
            raise QueryError("INVALID_AGGREGATION: use at most 10 metrics with unique labels.")
        metric_geometry = "geometry"
        if clip_geometry:
            if polygon is not None:
                area_sql = "ST_GeomFromGeoJSON('" + polygon.model_dump_json() + "')"
            elif radius_m is not None and center is not None:
                area_sql = (
                    f"ST_Transform(ST_Buffer(ST_Point(0,0), {float(radius_m)}, 32), "
                    f"'{projection(center)}', 'EPSG:4326', always_xy := true)"
                )
            else:
                b = plan.bounds
                area_sql = f"ST_MakeEnvelope({b.west},{b.south},{b.east},{b.north})"
            metric_geometry = f"ST_Intersection(geometry, {area_sql})"
        metric_select = []
        for index, aggregate in enumerate(aggregates):
            if aggregate.field.startswith("@"):
                expression = metric_expression(aggregate.field, metric_geometry, center)
                kind = "DOUBLE"
            else:
                expression, kind = self.field(plan.path, files, plan.columns, aggregate.field)
                if kind.endswith("[]") or kind.startswith(("STRUCT", "MAP", "GEOMETRY")):
                    raise QueryError("INVALID_AGGREGATION: select a scalar field.")
            if aggregate.op in {"sum", "avg"} and not re.match(
                r"^(U?TINYINT|U?SMALLINT|U?INTEGER|U?BIGINT|HUGEINT|FLOAT|DOUBLE|DECIMAL)", kind
            ):
                raise QueryError("INVALID_AGGREGATION: sum/avg require a numeric property.")
            operation = (
                "count(DISTINCT " if aggregate.op == "count_distinct" else aggregate.op + "("
            )
            metric_select.append(f"{operation}{expression}) AS metric_{index}")
        selection = "count(*) AS total" + (", " + ", ".join(metric_select) if metric_select else "")
        total_row = (
            self.query(
                f"SELECT {selection} FROM read_parquet(?) WHERE {plan.clause}", plan.parameters
            )[0]
            if plan.files
            else {"total": 0}
        )
        total = total_row["total"]
        metric_values = {
            a.label: total_row.get(
                f"metric_{i}", 0 if a.op in {"count", "count_distinct"} else None
            )
            for i, a in enumerate(aggregates)
        }
        rows = []
        if groups and plan.files:
            group_select = ", ".join(f"{e} AS group_{i}" for i, e in enumerate(expressions))
            names = ", ".join(f"group_{i}" for i in range(len(groups)))
            ordering = ", ".join(f"group_{i} NULLS LAST" for i in range(len(groups)))
            rows = self.query(
                f"SELECT {group_select}, {selection} FROM read_parquet(?) WHERE {plan.clause} "
                f"GROUP BY {names} ORDER BY total DESC, {ordering} LIMIT 51",
                plan.parameters,
            )
        formatted: list[dict[str, Any]] = []
        for row in rows[:50]:
            entry = {
                "value": row["group_0"]
                if len(groups) == 1
                else {f: row[f"group_{i}"] for i, f in enumerate(groups)},
                "count": row["total"],
            }
            if aggregates:
                entry["metrics"] = {a.label: row[f"metric_{i}"] for i, a in enumerate(aggregates)}
            formatted.append(entry)
        data = {
            "total": total,
            "group_by": group_by,
            "groups": formatted,
            "groups_truncated": len(rows) > 50,
            "other_count": total - sum(g["count"] for g in formatted) if groups else None,
            "metrics": metric_values,
            "metric_definitions": [a.model_dump() for a in aggregates],
            "metric_scope": "clipped_to_query" if clip_geometry else "whole_feature",
            "query": self.fingerprint(plan),
            "units": {"@area_m2": "m²", "@length_m": "m", "@distance_m": "m"},
        }
        if center is not None:
            data["distance_method"] = DISTANCE_METHOD
        if radius_m is not None and clip_geometry and aggregates:
            data["circle_clip_segments"] = 128
        return self.response(plan.release, theme, feature_type, plan.path, plan.bounds, data)

    @staticmethod
    def identifier(value: str) -> str:
        try:
            return str(UUID(value))
        except (ValueError, AttributeError) as exc:
            raise QueryError("INVALID_ID: use a valid UUID.") from exc

    def check_candidates(self, plan: Plan) -> int:
        count = (
            self.query(
                f"SELECT count(*) AS total FROM read_parquet(?) WHERE {plan.clause}",
                plan.parameters,
            )[0]["total"]
            if plan.files
            else 0
        )
        if count > MAX_CANDIDATES:
            raise QueryError(
                f"TOO_MANY_CANDIDATES: {count} features; narrow bounds/filters to "
                f"<= {MAX_CANDIDATES} per dataset. No partial analysis returned."
            )
        return count

    def get_feature(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds | None,
        release: str | None,
        identifier: str,
        include_geometry: bool = True,
        fields: list[str] | None = None,
    ) -> Response:
        if bounds is not None:
            return self.search(
                theme,
                feature_type,
                bounds,
                release,
                identifier=identifier,
                include_geometry=include_geometry,
                fields=fields,
                limit=1,
            )
        resolved = self.resolve_id(identifier, include_geometry, fields)
        selected, _ = self.dataset(theme, feature_type, release)
        if resolved.data["status"] != "live":
            raise QueryError(
                "BOUNDS_REQUIRED: ID is removed or outside the current GERS registry; "
                "supply known bounds/release for a scoped lookup."
            )
        if (
            resolved.theme != theme
            or resolved.feature_type != feature_type
            or resolved.release != selected
        ):
            raise QueryError(
                "ID_SCOPE_MISMATCH: use the resolved theme/type/current release, "
                "or supply bounds for a historical lookup."
            )
        resolved.data = {
            "features": [resolved.data["feature"]],
            "returned": 1,
            "geometry_included": include_geometry,
            "registry": resolved.data["registry"],
        }
        return resolved

    def json_properties(self, plan: Plan, fields: list[str] | None) -> str:
        members = ", ".join(
            f"{quote(name)} := {expr}" for name, expr in self.properties(plan, fields)
        )
        return f"to_json(struct_pack({members}))"

    def resolve_id(
        self, identifier: str, include_geometry: bool = True, fields: list[str] | None = None
    ) -> Response:
        identifier = self.identifier(identifier)
        release, _ = self.catalog.resolve()
        records = self.query(
            "SELECT id, version, first_seen, last_seen, last_changed, path, bbox "
            "FROM read_parquet(?) WHERE id = ?",
            [REGISTRY, identifier],
        )
        if not records or records[0]["path"] is None:
            return Response(
                release=release,
                source=REGISTRY,
                license="Theme/source-specific",
                data=bounded(
                    {
                        "status": "removed" if records else "not_in_registry",
                        "registry": records[0] if records else None,
                        "feature": None,
                    }
                ),
                warnings=[
                    "Registry excludes Addresses, Base and building_part. "
                    "Absence is not global feature nonexistence."
                ],
            )
        record = records[0]
        match = re.fullmatch(
            r"theme=([a-z_]+)/type=([a-z_]+)/part-[\w.-]+\.parquet", record["path"]
        )
        if (
            match is None
            or record["last_seen"] != release
            or match[1] not in TYPES
            or match[2] not in TYPES[match[1]]
        ):
            raise QueryError(
                "REGISTRY_CHANGED: cannot verify current path/release; refresh catalog and retry."
            )
        theme, kind = cast(Theme, match[1]), match[2]
        _, path = self.dataset(theme, kind, release)
        file = f"s3://overturemaps-us-west-2/release/{release}/{record['path']}"
        columns = self.columns(path, [file])
        # Single-ID lookup uses a verified file; it does not scan a global feature dataset.
        plan = Plan(
            release,
            path,
            [file],
            columns,
            Bounds(west=0, east=0.001, south=0, north=0.001),
            "id = ?",
            [[file], identifier],
            {},
            [],
        )
        projections = [f"{expr} AS {quote(name)}" for name, expr in self.properties(plan, fields)]
        if include_geometry:
            projections.append("ST_AsGeoJSON(geometry) AS geometry")
        features = self.query(
            f"SELECT {', '.join(projections)} FROM read_parquet(?) WHERE id = ? LIMIT 1",
            [[file], identifier],
        )
        if not features:
            raise QueryError(
                "REGISTRY_CHANGED: registry and feature disagree; refresh catalog and retry."
            )
        feature = features[0]
        if include_geometry and feature.get("geometry"):
            feature["geometry"] = json.loads(feature["geometry"])
        return self.response(
            release,
            theme,
            kind,
            file,
            None,
            {"status": "live", "registry_source": REGISTRY, "registry": record, "feature": feature},
        )

    def spatial_join(
        self,
        left: Dataset,
        right: Dataset,
        bounds: Bounds,
        release: str | None = None,
        relation: str = "intersects",
        distance_m: float | None = None,
        mode: str = "pairs",
        limit: int = 20,
        cursor: str | None = None,
    ) -> Response:
        if not 1 <= limit <= 50 or mode not in {"pairs", "count", "group_left"}:
            raise QueryError("INVALID_JOIN: choose pairs/count/group_left and limit 1..50.")
        selected, _ = self.catalog.resolve(release)
        left_plan = self.prepare(left.theme, left.feature_type, bounds, selected, left.filters)
        right_plan = self.prepare(right.theme, right.feature_type, bounds, selected, right.filters)
        left_count = self.check_candidates(left_plan)
        right_count = self.check_candidates(right_plan)
        functions = {
            "intersects": "ST_Intersects",
            "within": "ST_Within",
            "contains": "ST_Contains",
            "touches": "ST_Touches",
            "overlaps": "ST_Overlaps",
        }
        center = Point(
            longitude=(bounds.west + bounds.east) / 2, latitude=(bounds.south + bounds.north) / 2
        )
        crs = projection(center)
        distance = (
            f"ST_Distance(ST_Transform(l.geometry, 'EPSG:4326', '{crs}', always_xy := true), "
            f"ST_Transform(r.geometry, 'EPSG:4326', '{crs}', always_xy := true))"
        )
        if relation == "within_distance":
            if distance_m is None or not 0 <= distance_m <= 25000:
                raise QueryError("INVALID_DISTANCE: within_distance requires 0..25000 meters.")
            predicate = distance + " <= ?"
            extra = [distance_m]
        elif relation in functions and distance_m is None:
            predicate, extra = functions[relation] + "(l.geometry,r.geometry)", []
        else:
            raise QueryError(
                "INVALID_RELATION: use intersects/within/contains/touches/overlaps/within_distance."
            )
        cte = (
            "WITH l AS (SELECT id, geometry, "
            f"{self.json_properties(left_plan, left.fields)} AS properties "
            f"FROM read_parquet(?) WHERE {left_plan.clause}), "
            "r AS (SELECT id, geometry, "
            f"{self.json_properties(right_plan, right.fields)} AS properties "
            f"FROM read_parquet(?) WHERE {right_plan.clause}) "
        )
        params = [*left_plan.parameters, *right_plan.parameters, *extra]
        data: dict[str, Any] = {
            "left": left.model_dump(),
            "right": right.model_dump(),
            "left_candidates": left_count,
            "right_candidates": right_count,
            "relation": relation,
            "mode": mode,
            "right_source": right_plan.path,
            "right_license": LICENSES[right.theme],
            "orientation": "relation(left, right); both datasets restricted to bounds",
        }
        key = cursor_key(
            selected,
            left.theme,
            left.feature_type,
            data | {"bounds": bounds.model_dump(), "distance_m": distance_m},
        )
        after = page_decode(cursor, key, 2 if mode == "pairs" else 1)
        token = None
        if not left_count or not right_count:
            if mode == "group_left" and left_count:
                # Preserve zero-match left features with bounded pagination.
                continuation = " AND id > ?" if after else ""
                rows = self.query(
                    f"SELECT id AS left_id, 0 AS count FROM read_parquet(?) "
                    f"WHERE {left_plan.clause}{continuation} ORDER BY id LIMIT ?",
                    [*left_plan.parameters, *(after or []), limit + 1],
                )
                more, rows = len(rows) > limit, rows[:limit]
                token = page_encode([rows[-1]["left_id"]], key) if more else None
                data["groups"] = rows
            else:
                data["pairs" if mode == "pairs" else "groups"] = []
            data["total_pairs"] = 0
        else:
            data["total_pairs"] = self.query(
                cte + f"SELECT count(*) AS total FROM l JOIN r ON {predicate}", params
            )[0]["total"]
            if mode == "pairs":
                continuation = "WHERE (l.id,r.id) > (?,?)" if after else ""
                rows = self.query(
                    cte + "SELECT l.id AS left_id, r.id AS right_id, "
                    "l.properties AS left_properties, r.properties AS right_properties, "
                    f"{distance if relation == 'within_distance' else 'NULL'} AS distance_m "
                    f"FROM l JOIN r ON {predicate} {continuation} ORDER BY l.id,r.id LIMIT ?",
                    [*params, *(after or []), limit + 1],
                )
                more, rows = len(rows) > limit, rows[:limit]
                token = (
                    page_encode([rows[-1]["left_id"], rows[-1]["right_id"]], key) if more else None
                )
                for row in rows:
                    row["left_properties"] = json.loads(row["left_properties"])
                    row["right_properties"] = json.loads(row["right_properties"])
                data["pairs"] = rows
            elif mode == "group_left":
                continuation = "WHERE l.id > ?" if after else ""
                rows = self.query(
                    cte + "SELECT l.id AS left_id, count(r.id) AS count "
                    f"FROM l LEFT JOIN r ON {predicate} {continuation} "
                    "GROUP BY l.id ORDER BY l.id LIMIT ?",
                    [*params, *(after or []), limit + 1],
                )
                more, rows = len(rows) > limit, rows[:limit]
                token = page_encode([rows[-1]["left_id"]], key) if more else None
                data["groups"] = rows
        if relation == "within_distance":
            data["distance_method"] = (
                "Minimum distance in a local WGS84 azimuthal-equidistant projection; "
                "projected approximation."
            )
            data["distance_limit_m"] = distance_m
        return self.response(
            selected, left.theme, left.feature_type, left_plan.path, bounds, data, token
        )

    def compare_releases(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds,
        before: str,
        after: str,
        filters: list[AttributeFilter] | None = None,
        fields: list[str] | None = None,
        status: str = "all",
        limit: int = 20,
        cursor: str | None = None,
    ) -> Response:
        if not 1 <= limit <= 50 or status not in {"all", "added", "removed", "modified"}:
            raise QueryError(
                "INVALID_COMPARISON: status all/added/removed/modified and limit 1..50."
            )
        old = self.prepare(theme, feature_type, bounds, before, filters)
        new = self.prepare(theme, feature_type, bounds, after, filters)
        old_count, new_count = self.check_candidates(old), self.check_candidates(new)
        union_fields = sorted((set(old.columns) | set(new.columns)) - {"geometry"})

        def comparison_properties(plan: Plan) -> str:
            return (
                "struct_pack("
                + ", ".join(
                    f"{quote(f)} := {quote(f) if f in plan.columns else 'NULL'}"
                    for f in union_fields
                )
                + ")"
            )

        # Empty manifests still need a typed empty relation, using a schema shard.
        old_params, new_params = list(old.parameters), list(new.parameters)
        if not old.files:
            old_params[0] = self.catalog.files(before, theme, feature_type)
        if not new.files:
            new_params[0] = self.catalog.files(after, theme, feature_type)
        cte = (
            f"WITH a AS (SELECT id, geometry, {comparison_properties(old)} AS fingerprint, "
            f"{self.json_properties(old, fields)} AS properties FROM read_parquet(?) "
            f"WHERE {'FALSE' if not old.files else old.clause}), "
            f"b AS (SELECT id, geometry, {comparison_properties(new)} AS fingerprint, "
            f"{self.json_properties(new, fields)} AS properties FROM read_parquet(?) "
            f"WHERE {'FALSE' if not new.files else new.clause}), "
            "changes AS (SELECT coalesce(a.id,b.id) AS id, CASE "
            "WHEN a.id IS NULL THEN 'added' WHEN b.id IS NULL THEN 'removed' "
            "WHEN a.fingerprint IS DISTINCT FROM b.fingerprint "
            "OR NOT coalesce(ST_Equals(a.geometry,b.geometry), FALSE) THEN 'modified' "
            "ELSE 'unchanged' END AS status, a.properties AS before_properties, "
            "b.properties AS after_properties FROM a FULL OUTER JOIN b ON a.id=b.id) "
        )
        # Remove scope parameters for FALSE relations; otherwise DuckDB sees excess binds.
        params = (old_params if old.files else [old_params[0]]) + (
            new_params if new.files else [new_params[0]]
        )
        counts = self.query(
            cte + "SELECT status,count(*) AS count FROM changes GROUP BY status", params
        )
        totals = dict.fromkeys(("added", "removed", "modified", "unchanged"), 0)
        totals.update({r["status"]: r["count"] for r in counts})
        key = cursor_key(
            after,
            theme,
            feature_type,
            {"before": before, "query": self.fingerprint(new), "fields": fields, "status": status},
        )
        continuation = page_decode(cursor, key, 1)
        clause = "status != 'unchanged'" if status == "all" else "status = ?"
        extra = [] if status == "all" else [status]
        if continuation:
            clause += " AND id > ?"
            extra.extend(continuation)
        rows = self.query(
            cte + f"SELECT * FROM changes WHERE {clause} ORDER BY id LIMIT ?",
            [*params, *extra, limit + 1],
        )
        more, rows = len(rows) > limit, rows[:limit]
        token = page_encode([rows[-1]["id"]], key) if more else None
        for row in rows:
            for field in ("before_properties", "after_properties"):
                row[field] = json.loads(row[field]) if row[field] is not None else None
        data = {
            "before_release": before,
            "after_release": after,
            "before_source": old.path,
            "before_count": old_count,
            "after_count": new_count,
            "counts": totals,
            "changes": rows,
            "returned": len(rows),
            "query": self.fingerprint(new),
            "schema_added": sorted(set(new.columns) - set(old.columns)),
            "schema_removed": sorted(set(old.columns) - set(new.columns)),
            "schema_type_changes": {
                field: {"before": old.columns[field], "after": new.columns[field]}
                for field in old.columns.keys() & new.columns.keys()
                if old.columns[field] != new.columns[field]
            },
            "semantics": (
                "ID comparison of filtered snapshots intersecting bounds; "
                "added/removed may mean entering/leaving scope or filters. "
                "Geometry uses topological equality; properties include version/sources."
            ),
        }
        result = self.response(after, theme, feature_type, new.path, bounds, data, token)
        result.warnings.append(
            "This is a bounded snapshot comparison, not the global GERS changelog "
            "or proof of real-world creation/deletion."
        )
        return result
