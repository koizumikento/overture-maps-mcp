from __future__ import annotations

from functools import wraps
from typing import Any, get_type_hints

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import ValidationError

from overture_maps_mcp import _OPERATIONS
from overture_maps_mcp.api import Client
from overture_maps_mcp.models import QueryError, Response


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
client = Client()
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True
)


def adapt(method):
    @wraps(method)
    def tool(*args, **kwargs) -> Response:
        try:
            return method(*args, **kwargs)
        except (QueryError, ValidationError) as exc:
            raise ToolError(str(exc)) from exc

    tool.__annotations__ = get_type_hints(method, include_extras=True)
    return tool


for operation in _OPERATIONS:
    mcp.tool(name=f"overture_{operation}", annotations=READ_ONLY)(adapt(getattr(client, operation)))
