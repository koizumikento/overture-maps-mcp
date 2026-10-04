from __future__ import annotations

from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from overture_maps_mcp.models import Bounds, QueryError, Response, Theme
from overture_maps_mcp.service import Service

mcp = MCPServer("overture-maps-mcp")
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
    bounds: Bounds,
    release: str | None = None,
    name: str | None = None,
    category: str | None = None,
    feature_class: str | None = None,
    min_confidence: Confidence | None = None,
    limit: Limit = 20,
    cursor: str | None = None,
    include_geometry: bool = False,
) -> Response:
    """Search intersecting features in a bounded WGS84 area across any of the six themes.

    Name is literal case-insensitive substring; category is exact taxonomy.primary (places).
    Confidence is places-only. Invalid filters produce errors rather than being ignored.
    Pass next_cursor unchanged with the same explicit release/filters for the next page.
    Returns IDs, properties, provenance; geometry is opt-in. No geocoding or routing.
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
    )


@mcp.tool(annotations=READ_ONLY)
def overture_get_feature(
    theme: Theme,
    feature_type: str,
    identifier: str,
    bounds: Bounds,
    release: str | None = None,
    include_geometry: bool = True,
) -> Response:
    """Get a UUID feature from search with its known bounding area and pinned release.

    Uses a bounded lookup, never a global ID scan. Empty means absent in this area/release,
    not globally nonexistent. Base/building_part IDs have no GERS stability commitment.
    """
    return run(
        service.search,
        theme,
        feature_type,
        bounds,
        release,
        include_geometry=include_geometry,
        identifier=identifier,
        limit=1,
    )


@mcp.tool(annotations=READ_ONLY)
def overture_summarize(
    theme: Theme,
    feature_type: str,
    bounds: Bounds,
    group_by: str | None = None,
    release: str | None = None,
) -> Response:
    """Count all dataset features intersecting a bounded area, optionally group by a column.

    Groups: class, subtype, basic_category, country, operating_status (when present).
    At most 50 groups; other_count reconciles omitted groups. No real-world completeness
    claim, no area/length weighting, no count extrapolated from a limited search page.
    """
    return run(service.summarize, theme, feature_type, bounds, group_by, release)
