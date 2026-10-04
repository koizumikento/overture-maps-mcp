"""Overture Maps geographic search for Python, CLI and optional MCP."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from overture_maps_mcp.api import Client as Client
    from overture_maps_mcp.models import (
        Aggregation as Aggregation,
    )
    from overture_maps_mcp.models import (
        AttributeFilter as AttributeFilter,
    )
    from overture_maps_mcp.models import (
        Bounds as Bounds,
    )
    from overture_maps_mcp.models import (
        Dataset as Dataset,
    )
    from overture_maps_mcp.models import (
        Point as Point,
    )
    from overture_maps_mcp.models import (
        Polygon as Polygon,
    )
    from overture_maps_mcp.models import (
        QueryError as QueryError,
    )
    from overture_maps_mcp.models import (
        Response as Response,
    )

__version__ = "0.3.0"
_OPERATIONS = (
    "catalog",
    "schema",
    "search",
    "get_feature",
    "summarize",
    "nearest",
    "spatial_join",
    "compare_releases",
    "resolve_id",
)
__all__ = [
    "Aggregation",
    "AttributeFilter",
    "Bounds",
    "Client",
    "Dataset",
    "Point",
    "Polygon",
    "QueryError",
    "Response",
]


def __getattr__(name: str):
    # Keep manage.py/storage/clean dependency-free; ordinary imports load only the core.
    if name == "Client":
        from overture_maps_mcp.api import Client

        return Client
    if name in __all__:
        from overture_maps_mcp import models

        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
