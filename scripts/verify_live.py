"""Explicit live read-only smoke test; not part of the default test suite."""

import asyncio
import json
import sys
from importlib.metadata import version

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main() -> None:
    transport = StdioServerParameters(command=sys.executable, args=["-m", "overture_maps_mcp"])
    async with Client(transport) as client:
        print(
            json.dumps(
                {
                    "transport": "stdio",
                    "protocol": client.protocol_version,
                    "mcp": version("mcp"),
                    "duckdb": version("duckdb"),
                }
            ),
            flush=True,
        )
        tools = (await client.list_tools()).tools
        assert len(tools) == 5
        catalog = await client.call_tool("overture_catalog", {})
        assert not catalog.is_error and catalog.structured_content is not None
        release = catalog.structured_content["release"]
        bounds = {"west": 139.760, "south": 35.680, "east": 139.762, "north": 35.682}
        selections = {
            "addresses": "address",
            "base": "land_use",
            "buildings": "building",
            "divisions": "division_area",
            "places": "place",
            "transportation": "segment",
        }
        for theme, kind in selections.items():
            schema = await client.call_tool(
                "overture_schema",
                {
                    "theme": theme,
                    "feature_type": kind,
                    "release": release,
                },
            )
            assert not schema.is_error, schema.content
            result = await client.call_tool(
                "overture_search",
                {
                    "theme": theme,
                    "feature_type": kind,
                    "release": release,
                    "bounds": bounds,
                    "limit": 2,
                },
            )
            assert not result.is_error, result.content
            data = result.structured_content
            assert data is not None
            print(
                json.dumps(
                    {
                        "theme": theme,
                        "type": kind,
                        "release": release,
                        "returned": data["data"]["returned"],
                        "next_page": data["next_cursor"] is not None,
                    }
                ),
                flush=True,
            )
            if data["data"]["features"]:
                identifier = data["data"]["features"][0]["id"]
                detail = await client.call_tool(
                    "overture_get_feature",
                    {
                        "theme": theme,
                        "feature_type": kind,
                        "release": release,
                        "bounds": bounds,
                        "identifier": identifier,
                    },
                )
                assert not detail.is_error, detail.content
                assert detail.structured_content is not None
                assert detail.structured_content["data"]["features"][0]["id"] == identifier
            if data["next_cursor"]:
                second = await client.call_tool(
                    "overture_search",
                    {
                        "theme": theme,
                        "feature_type": kind,
                        "release": release,
                        "bounds": bounds,
                        "limit": 2,
                        "cursor": data["next_cursor"],
                    },
                )
                assert not second.is_error, second.content
                assert second.structured_content is not None
                first_ids = {row["id"] for row in data["data"]["features"]}
                second_ids = {row["id"] for row in second.structured_content["data"]["features"]}
                assert first_ids.isdisjoint(second_ids)
        summary = await client.call_tool(
            "overture_summarize",
            {
                "theme": "places",
                "feature_type": "place",
                "release": release,
                "bounds": bounds,
                "group_by": "basic_category",
            },
        )
        assert not summary.is_error, summary.content
        assert summary.structured_content is not None
        print(json.dumps({"places_summary": summary.structured_content["data"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
