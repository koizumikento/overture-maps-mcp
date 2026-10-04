from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from overture_maps_mcp.models import (
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
from overture_maps_mcp.service import Service


class StrictMCPServer(MCPServer):
    """Reject misspelled top-level conditions before the SDK discards extra arguments."""

    async def list_tools(self):
        registered = await super().list_tools()
        for tool in registered:
            tool.input_schema = {**tool.input_schema, "additionalProperties": False}
        return registered

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
        if tool is not None:
            unknown = set(arguments) - set(tool.input_schema.get("properties", {}))
            if unknown:
                raise ToolError(
                    "UNKNOWN_ARGUMENT: use declared parameters; "
                    "put attribute conditions in filters."
                )
        return await super().call_tool(name, arguments, context)


mcp = StrictMCPServer("overture-maps-mcp")
service = Service()
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)
Limit = Annotated[int, Field(ge=1, le=50)]
Confidence = Annotated[float, Field(ge=0, le=1)]


def run(operation, *args, **kwargs) -> Response:
    try:
        return operation(*args, **kwargs)
    except QueryError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=READ_ONLY)
def overture_catalog() -> Response:
    """Discover all six themes/types and available releases. Pin a release for later calls.

    Use before searching; does not query geographic features or resolve an address.
    """
    return run(service.info)


@mcp.tool(annotations=READ_ONLY)
def overture_schema(theme: Theme, feature_type: str, release: str | None = None) -> Response:
    """Inspect dataset columns for a theme/type before filtering or grouping.

    Use with a pair from overture_catalog; reads Parquet metadata, not feature rows.
    """
    return run(service.schema, theme, feature_type, release)


@mcp.tool(annotations=READ_ONLY)
def overture_search(
    theme: Theme,
    feature_type: str,
    bounds: Bounds | None = None,
    release: str | None = None,
    name: str | None = None,
    category: str | None = None,
    feature_class: str | None = None,
    min_confidence: Confidence | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
    include_geometry: bool = False,
    filters: list[AttributeFilter] | None = None,
    fields: list[str] | None = None,
    center: Point | None = None,
    radius_m: Annotated[float, Field(ge=1, le=25000)] | None = None,
    polygon: Polygon | None = None,
    sort_by: Literal["id", "distance"] = "id",
    include_metrics: bool = False,
    output_format: Literal["features", "geojson"] = "features",
) -> Response:
    """Search intersecting features in a bounded WGS84 area across any of the six themes.

    Name is literal case-insensitive substring; category is exact taxonomy.primary (places).
    Confidence is places-only. Invalid filters produce errors rather than being ignored.
    Pass next_cursor unchanged with the same explicit release/filters for the next page.
    Choose bounds, center/radius_m, or GeoJSON Polygon/MultiPolygon (holes supported).
    All properties are available; fields selects paths. filters combines up to 20 typed
    AND conditions, including nested/list paths (sources[].dataset). No user SQL.
    Distance order needs center; include_metrics returns whole-feature m² / line meters.
    geojson returns a paginated FeatureCollection; retain the response provenance.
    """
    return run(
        service.search,
        theme,
        feature_type,
        bounds,
        release,
        name,
        category,
        feature_class,
        min_confidence,
        limit,
        cursor,
        include_geometry,
        filters=filters,
        fields=fields,
        center=center,
        radius_m=radius_m,
        polygon=polygon,
        sort_by=sort_by,
        include_metrics=include_metrics,
        output_format=output_format,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_get_feature(
    theme: Theme,
    feature_type: str,
    identifier: str,
    bounds: Bounds | None = None,
    release: str | None = None,
    include_geometry: bool = True,
    fields: list[str] | None = None,
) -> Response:
    """Get a UUID feature from search with its known bounding area and pinned release.

    With bounds, lookup is pinned to that area/release. Without bounds, uses the current
    GERS registry and its verified shard, and requires the matching theme/type/current
    release. Non-GERS types and historical releases need bounds. Empty scoped results
    do not establish global nonexistence. fields chooses properties; default is all.
    """
    return run(
        service.get_feature,
        theme,
        feature_type,
        bounds,
        release,
        identifier,
        include_geometry=include_geometry,
        fields=fields,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_summarize(
    theme: Theme,
    feature_type: str,
    bounds: Bounds | None = None,
    group_by: str | list[str] | None = None,
    release: str | None = None,
    filters: list[AttributeFilter] | None = None,
    name: str | None = None,
    category: str | None = None,
    feature_class: str | None = None,
    min_confidence: Confidence | None = None,
    center: Point | None = None,
    radius_m: Annotated[float, Field(ge=1, le=25000)] | None = None,
    polygon: Polygon | None = None,
    aggregations: list[Aggregation] | None = None,
    clip_geometry: bool = True,
) -> Response:
    """Count all dataset features intersecting a bounded area, optionally group by a column.

    Accepts exactly the same area/attribute conditions as search. Group by up to 3 scalar
    property paths; top 50 groups with other_count. Aggregate sum/avg/min/max/count/
    count_distinct of properties or @area_m2/@length_m/@distance_m (needs center).
    Geometry metrics default to clipped scope; circles use a 128-segment clip polygon.
    Counts and property aggregates are over whole intersecting records, not prorated.
    """
    return run(
        service.summarize,
        theme,
        feature_type,
        bounds,
        group_by,
        release,
        filters,
        name,
        category,
        feature_class,
        min_confidence,
        center,
        radius_m,
        polygon,
        aggregations,
        clip_geometry,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_nearest(
    theme: Theme,
    feature_type: str,
    center: Point,
    radius_m: Annotated[float, Field(ge=1, le=25000)],
    release: str | None = None,
    filters: list[AttributeFilter] | None = None,
    fields: list[str] | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
    include_geometry: bool = False,
) -> Response:
    """Find the nearest intersecting features within a maximum radius; distance in meters.

    Searches points, lines and polygons; polygon containing center has zero distance.
    Stable order is distance then ID. Point distances are geodesic; line/polygon edges
    use a local azimuthal-equidistant projection. Empty means none within the radius.
    """
    return run(
        service.search,
        theme,
        feature_type,
        release=release,
        filters=filters,
        fields=fields,
        center=center,
        radius_m=radius_m,
        sort_by="distance",
        limit=limit,
        cursor=cursor,
        include_geometry=include_geometry,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_spatial_join(
    left: Dataset,
    right: Dataset,
    bounds: Bounds,
    release: str | None = None,
    relation: Literal[
        "intersects", "within", "contains", "touches", "overlaps", "within_distance"
    ] = "intersects",
    distance_m: Annotated[float, Field(ge=0, le=25000)] | None = None,
    mode: Literal["pairs", "count", "group_left"] = "pairs",
    limit: Limit = 20,
    cursor: str | None = None,
) -> Response:
    """Relate features across themes/types in one release and bounded area.

    Relation is left-to-right: places within division_area, or areas containing places.
    Both datasets are scoped to bounds; <=5000 candidates each or fail without partial
    analysis. pairs paginates pairs with properties/provenance; count returns total pairs;
    group_left returns every left ID with match count, including zero. within_distance
    needs distance_m and uses a local projected minimum geometry distance in meters.
    """
    return run(
        service.spatial_join,
        left,
        right,
        bounds,
        release,
        relation,
        distance_m,
        mode,
        limit,
        cursor,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_compare_releases(
    theme: Theme,
    feature_type: str,
    bounds: Bounds,
    before: str,
    after: str,
    filters: list[AttributeFilter] | None = None,
    fields: list[str] | None = None,
    status: Literal["all", "added", "removed", "modified"] = "all",
    limit: Limit = 20,
    cursor: str | None = None,
) -> Response:
    """Compare two available release snapshots by ID in the same area and filters.

    Returns exact added/removed/modified/unchanged counts and paginated changes, including
    before/after properties and schema changes. Compares all properties regardless of
    fields selection, plus topological geometry equality. <=5000 records per snapshot.
    Added/removed can mean crossing scope/filters; not global creation/deletion. IDs in
    Addresses/Base/building_part lack GERS stability. Use catalog for available releases.
    """
    return run(
        service.compare_releases,
        theme,
        feature_type,
        bounds,
        before,
        after,
        filters,
        fields,
        status,
        limit,
        cursor,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_resolve_id(
    identifier: str,
    include_geometry: bool = True,
    fields: list[str] | None = None,
) -> Response:
    """Resolve a UUID without knowing its theme or position using the current GERS registry.

    Returns live feature and verified file, removed ID metadata, or not_in_registry.
    Registry is unversioned and excludes Addresses/Base/building_part. For those IDs or
    historical releases use overture_get_feature with known bounds. Never infer global
    nonexistence from not_in_registry. Default returns all properties and geometry.
    """
    return run(service.resolve_id, identifier, include_geometry, fields)
