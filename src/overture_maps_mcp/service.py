from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from overture_maps_mcp.catalog import CATALOG_URL, Catalog, get_catalog
from overture_maps_mcp.models import LICENSES, TYPES, Bounds, QueryError, Response, Theme
from overture_maps_mcp.query import (
    Query,
    bbox_filter,
    bounded,
    cursor_key,
    decode_cursor,
    encode_cursor,
    execute,
)

SUMMARY_FIELDS = {"class", "subtype", "basic_category", "country", "operating_status"}
DETAIL_FIELDS = (
    "id",
    "version",
    "names",
    "class",
    "subtype",
    "basic_category",
    "taxonomy",
    "confidence",
    "operating_status",
    "addresses",
    "address_levels",
    "street",
    "number",
    "postcode",
    "country",
    "height",
    "num_floors",
    "division_id",
    "hierarchies",
    "sources",
    "road_surface",
    "road_flags",
    "access_restrictions",
    "speed_limits",
    "connectors",
    "websites",
    "phones",
    "brand",
    "bbox",
)


class Service:
    def __init__(self, catalog: Catalog | None = None, query: Query = execute) -> None:
        self.catalog = catalog or get_catalog()
        self.query = query
        self._columns: dict[str, dict[str, str]] = {}

    def dataset(self, theme: Theme, feature_type: str, release: str | None) -> tuple[str, str]:
        if theme not in TYPES or feature_type not in TYPES[theme]:
            raise QueryError("INVALID_TYPE: call overture_catalog for valid theme/type pairs.")
        selected, _ = self.catalog.resolve(release)
        path = (
            f"s3://overturemaps-us-west-2/release/{selected}/"
            f"theme={theme}/type={feature_type}/*.parquet"
        )
        return selected, path

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
            },
            warnings=[
                "Pin the returned release for multi-tool workflows and pagination.",
                "Public data releases are retained for up to 60 days.",
            ],
        )

    def columns(self, path: str, files: list[str]) -> dict[str, str]:
        if path not in self._columns:
            rows = self.query("DESCRIBE SELECT * FROM read_parquet(?)", [files])
            self._columns[path] = {row["column_name"]: row["column_type"] for row in rows}
        return self._columns[path]

    def schema(self, theme: Theme, feature_type: str, release: str | None) -> Response:
        selected, path = self.dataset(theme, feature_type, release)
        files = self.catalog.files(selected, theme, feature_type)
        return self.response(
            selected, theme, feature_type, path, None, {"columns": self.columns(path, files)}
        )

    def search(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds,
        release: str | None = None,
        name: str | None = None,
        category: str | None = None,
        feature_class: str | None = None,
        min_confidence: float | None = None,
        limit: int = 20,
        cursor: str | None = None,
        include_geometry: bool = False,
        identifier: str | None = None,
    ) -> Response:
        if not 1 <= limit <= 50:
            raise QueryError("INVALID_LIMIT: use 1 to 50.")
        if min_confidence is not None and not 0 <= min_confidence <= 1:
            raise QueryError("INVALID_CONFIDENCE: use a value between 0 and 1.")
        selected, path = self.dataset(theme, feature_type, release)
        filters = {
            "bounds": bounds.model_dump(),
            "name": name,
            "category": category,
            "feature_class": feature_class,
            "min_confidence": min_confidence,
            "include_geometry": include_geometry,
            "identifier": identifier,
        }
        key = cursor_key(selected, theme, feature_type, filters)
        after = decode_cursor(cursor, key) if cursor else None
        cols = self.columns(path, self.catalog.files(selected, theme, feature_type))
        files = self.catalog.files(selected, theme, feature_type, bounds)
        clause, params = bbox_filter(bounds)
        params.insert(0, files)
        for value, column, expression in (
            (name, "names", "contains(lower(names.primary), lower(?))"),
            (category, "taxonomy", "taxonomy.primary = ?"),
            (feature_class, "class", '"class" = ?'),
            (min_confidence, "confidence", "confidence >= ?"),
        ):
            if value is not None:
                if column not in cols:
                    raise QueryError(f"UNSUPPORTED_FILTER: {column} is unavailable in this type.")
                clause += f" AND {expression}"
                params.append(value)
        if identifier is not None:
            try:
                identifier = str(UUID(identifier))
            except (ValueError, AttributeError) as exc:
                raise QueryError("INVALID_ID: use the UUID returned by a search.") from exc
            clause += " AND id = ?"
            params.append(identifier)
        if after:
            clause += " AND id > ?"
            params.append(after)
        projections = [f'"{field}"' for field in DETAIL_FIELDS if field in cols]
        if include_geometry:
            projections.append("ST_AsGeoJSON(geometry) AS geometry")
        rows = (
            self.query(
                f"SELECT {', '.join(projections)} FROM read_parquet(?) "
                f"WHERE {clause} ORDER BY id LIMIT ?",
                [*params, limit + 1],
            )
            if files
            else []
        )
        more = len(rows) > limit
        rows = rows[:limit]
        for row in rows:
            if include_geometry and row.get("geometry") is not None:
                row["geometry"] = json.loads(row["geometry"])
        next_cursor = encode_cursor(str(rows[-1]["id"]), key) if more else None
        return self.response(
            selected,
            theme,
            feature_type,
            path,
            bounds,
            {"features": rows, "returned": len(rows), "geometry_included": include_geometry},
            next_cursor,
        )

    def summarize(
        self,
        theme: Theme,
        feature_type: str,
        bounds: Bounds,
        group_by: str | None = None,
        release: str | None = None,
    ) -> Response:
        selected, path = self.dataset(theme, feature_type, release)
        files = self.catalog.files(selected, theme, feature_type, bounds)
        if group_by is not None and group_by not in SUMMARY_FIELDS:
            raise QueryError(
                "INVALID_GROUP: use class, subtype, basic_category, country or operating_status."
            )
        if group_by and group_by not in self.columns(
            path, self.catalog.files(selected, theme, feature_type)
        ):
            raise QueryError(
                "UNSUPPORTED_GROUP: call overture_schema and choose an existing column."
            )
        clause, parameters = bbox_filter(bounds)
        count = (
            self.query(
                f"SELECT count(*) AS total FROM read_parquet(?) WHERE {clause}",
                [files, *parameters],
            )[0]["total"]
            if files
            else 0
        )
        groups: list[dict[str, Any]] = []
        if group_by and files:
            groups = self.query(
                f'SELECT CAST("{group_by}" AS VARCHAR) AS value, count(*) AS count '
                f"FROM read_parquet(?) WHERE {clause} "
                "GROUP BY value ORDER BY count DESC, value NULLS LAST LIMIT 51",
                [files, *parameters],
            )
        truncated = len(groups) > 50
        groups = groups[:50]
        return self.response(
            selected,
            theme,
            feature_type,
            path,
            bounds,
            {
                "total": count,
                "group_by": group_by,
                "groups": groups,
                "groups_truncated": truncated,
                "other_count": count - sum(g["count"] for g in groups) if group_by else None,
            },
        )
