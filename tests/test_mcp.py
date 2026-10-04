import json

import pytest
from jsonschema import validate
from mcp import Client
from mcp.types import TextContent

from overture_maps_mcp import server
from overture_maps_mcp.models import Bounds
from overture_maps_mcp.service import Service
from tests.conftest import RELEASE


@pytest.mark.asyncio
async def test_mcp_contract_and_search_detail(
    monkeypatch, fixture_service: Service, bounds: Bounds
):
    monkeypatch.setattr(server.client, "_service", fixture_service)
    async with Client(server.mcp) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 9
        schemas = {tool.name: tool.output_schema for tool in tools}
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.open_world_hint is True
            assert tool.output_schema
            assert tool.input_schema["additionalProperties"] is False
        catalog = await client.call_tool("overture_catalog", {})
        assert not catalog.is_error
        data = catalog.structured_content
        assert data is not None
        validate(data, schemas["overture_catalog"])
        assert data["release"] == RELEASE
        params = {
            "theme": "places",
            "feature_type": "place",
            "bounds": bounds.model_dump(),
            "release": data["release"],
            "limit": 2,
        }
        result = await client.call_tool("overture_search", params)
        assert not result.is_error
        structured = result.structured_content
        assert structured is not None
        validate(structured, schemas["overture_search"])
        assert isinstance(result.content[0], TextContent)
        assert json.loads(result.content[0].text) == structured
        detail = await client.call_tool(
            "overture_get_feature",
            {
                "theme": "places",
                "feature_type": "place",
                "bounds": bounds.model_dump(),
                "release": data["release"],
                "identifier": structured["data"]["features"][0]["id"],
            },
        )
        assert not detail.is_error
        assert detail.structured_content is not None
        assert detail.structured_content["data"]["returned"] == 1
        invalid = await client.call_tool("overture_search", params | {"limit": 999})
        assert invalid.is_error
        wrong_type = await client.call_tool("overture_search", params | {"feature_type": "bad"})
        assert wrong_type.is_error
        assert isinstance(wrong_type.content[0], TextContent)
        assert "INVALID_TYPE" in wrong_type.content[0].text
        assert wrong_type.structured_content is None
        calls = []
        original_query = fixture_service.query

        def track_query(sql, parameters):
            calls.append(sql)
            return original_query(sql, parameters)

        monkeypatch.setattr(fixture_service, "query", track_query)
        misspelled = await client.call_tool("overture_search", params | {"country": "JP"})
        assert misspelled.is_error
        assert isinstance(misspelled.content[0], TextContent)
        assert "UNKNOWN_ARGUMENT" in misspelled.content[0].text
        assert calls == []
