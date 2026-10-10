import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import DiscoverResult, Implementation
from starlette.testclient import TestClient

from overture_maps_mcp import server
from overture_maps_mcp.backend import create_backend
from tests.test_analysis import AFTER, AREA, BEFORE, CENTER, analysis_service, uid

# Reuse the same synthetic geographic fixture as the API/stdio contract tests.
__all__ = ["analysis_service"]
TOKEN = "synthetic-service-secret-32-characters"
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


def test_backend_configuration():
    for token in ["", "short", "x" * 32 + "\n", "x" * 32 + "\0", "非" * 32]:
        with pytest.raises(ValueError, match="OVERTURE_BACKEND_TOKEN"):
            create_backend(token)


def test_backend_authorization():
    with TestClient(create_backend(TOKEN), base_url="http://127.0.0.1:8000") as client:
        for headers in [
            {},
            {"Authorization": "Bearer wrong"},
            {"oai-authenticated-user-id": "owner"},
        ]:
            assert client.post("/mcp", json={}, headers=headers).status_code == 401
        assert client.get("/mcp", headers=HEADERS).status_code == 405
        assert client.post("/else", json={}, headers=HEADERS).status_code == 404
        assert (
            client.post("/mcp", json={}, headers=HEADERS | {"Host": "evil.test"}).status_code == 421
        )
        assert (
            client.post(
                "/mcp", json={}, headers=HEADERS | {"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert (
            client.post("/mcp", content=b"x" * (256 * 1024 + 1), headers=HEADERS).status_code == 413
        )


def test_backend_and_worker_contract(analysis_service, monkeypatch, tmp_path):
    monkeypatch.setattr(server.client, "_service", analysis_service)
    cases = {
        "overture_catalog": {},
        "overture_schema": {"theme": "places", "feature_type": "place"},
        "overture_search": {
            "theme": "places",
            "feature_type": "place",
            "bounds": AREA.model_dump(),
            "limit": 1,
        },
        "overture_get_feature": {"theme": "places", "feature_type": "place", "identifier": uid(1)},
        "overture_summarize": {
            "theme": "places",
            "feature_type": "place",
            "bounds": AREA.model_dump(),
        },
        "overture_nearest": {
            "theme": "places",
            "feature_type": "place",
            "center": CENTER.model_dump(),
            "radius_m": 100,
        },
        "overture_spatial_join": {
            "left": {"theme": "places", "feature_type": "place"},
            "right": {"theme": "divisions", "feature_type": "division_area"},
            "bounds": AREA.model_dump(),
        },
        "overture_compare_releases": {
            "theme": "places",
            "feature_type": "place",
            "bounds": AREA.model_dump(),
            "before": BEFORE,
            "after": AFTER,
        },
        "overture_resolve_id": {"identifier": uid(1)},
    }

    async def baseline():
        async with Client(server.mcp) as client:
            tools = (await client.list_tools()).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            results = {
                name: (await client.call_tool(name, args)).model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                for name, args in cases.items()
            }
            return tools, results

    tools, results = asyncio.run(baseline())
    captures = []
    with TestClient(create_backend(TOKEN), base_url="http://127.0.0.1:8000") as client:

        def rpc(method, params):
            body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            response = client.post("/mcp", json=body, headers=HEADERS)
            assert response.status_code == 200, response.text
            assert "mcp-session-id" not in response.headers
            captures.append({"request": body, "response": response.json()})
            return response.json()["result"]

        initialized = rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "fixture", "version": "1"},
            },
        )
        assert initialized["protocolVersion"] == "2025-11-25"
        discovered = rpc("tools/list", {})
        # Ignore omitted None/default fields on the SDK wire; compare each registered definition.
        assert discovered["tools"] == tools["tools"]
        assert {t["name"] for t in discovered["tools"]} == set(cases)
        modern_meta = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "fixture", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
        for method, params in [
            ("server/discover", {}),
            ("tools/list", {}),
            ("tools/call", {"name": "overture_catalog", "arguments": {}}),
        ]:
            body = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": method,
                "params": params | {"_meta": modern_meta},
            }
            modern_headers = HEADERS | {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
            if method == "tools/call":
                modern_headers["Mcp-Name"] = "overture_catalog"
            response = client.post("/mcp", json=body, headers=modern_headers)
            assert response.status_code == 200, response.text
            envelope = response.json()
            assert envelope["jsonrpc"] == "2.0" and envelope["id"] == body["id"]
            assert "error" not in envelope, envelope
            result = envelope["result"]
            assert result["resultType"] == "complete"
            identity = Implementation.model_validate(
                result["_meta"]["io.modelcontextprotocol/serverInfo"]
            )
            assert identity.name == "overture-maps-mcp"
            assert isinstance(identity.version, str)
            if method == "server/discover":
                discovery = DiscoverResult.model_validate(result)
                assert result["supportedVersions"] == discovery.supported_versions == ["2026-07-28"]
                assert isinstance(result["capabilities"]["tools"], dict)
                assert discovery.capabilities.tools is not None
                assert result["ttlMs"] == 0 and result["cacheScope"] == "private"
            elif method == "tools/list":
                assert {tool["name"] for tool in result["tools"]} == set(cases)
            else:
                assert not result["isError"] and result["structuredContent"]["data"]["themes"]
            assert "mcp-session-id" not in response.headers
            captures.append({"request": body, "response": envelope, "protocol": "2026-07-28"})
        for name, args in cases.items():
            result = rpc("tools/call", {"name": name, "arguments": args})
            assert not result.get("isError", False), result
            assert result["structuredContent"] == results[name]["structuredContent"]
            assert result["content"] == results[name]["content"]
        search = rpc(
            "tools/call", {"name": "overture_search", "arguments": cases["overture_search"]}
        )
        assert search["structuredContent"]["next_cursor"]
        second = rpc(
            "tools/call",
            {
                "name": "overture_search",
                "arguments": cases["overture_search"]
                | {"cursor": search["structuredContent"]["next_cursor"]},
            },
        )
        assert (
            second["structuredContent"]["data"]["features"][0]["id"]
            != search["structuredContent"]["data"]["features"][0]["id"]
        )
        for theme, kind in [
            ("addresses", "address"),
            ("base", "land_use"),
            ("buildings", "building"),
            ("divisions", "division_area"),
            ("places", "place"),
            ("transportation", "segment"),
        ]:
            assert not rpc(
                "tools/call",
                {
                    "name": "overture_search",
                    "arguments": {
                        "theme": theme,
                        "feature_type": kind,
                        "bounds": AREA.model_dump(),
                    },
                },
            ).get("isError", False)
        for extra in [
            {"sql": "SELECT 1"},
            {"url": "https://evil.test"},
            {"limit": 999},
            {"cursor": "invalid"},
        ]:
            assert rpc(
                "tools/call",
                {"name": "overture_search", "arguments": cases["overture_search"] | extra},
            )["isError"]
    fixture = tmp_path / "sdk-wire.json"
    fixture.write_text(json.dumps(captures), encoding="utf-8")
    subprocess.run(["node", "sites/tests/relay-contract.mjs", str(fixture)], check=True)


def test_sites_artifact_package(tmp_path):
    source = Path(__file__).resolve().parents[1] / "sites"
    for directory in ["scripts", "worker", ".openai"]:
        shutil.copytree(source / directory, tmp_path / directory)
    manifest = tmp_path / ".openai" / "hosting.json"
    data = json.loads(manifest.read_text()) | {"project_id": "isolated-package-fixture"}
    manifest.write_text(json.dumps(data))
    subprocess.run(["node", str(tmp_path / "scripts" / "build.mjs"), "--archive"], check=True)
    assert (tmp_path / "dist" / "server" / "index.js").read_bytes() == (
        source / "worker" / "index.js"
    ).read_bytes()
    result = subprocess.run(
        ["tar", "-tzf", str(tmp_path / "site.tar.gz")], capture_output=True, text=True, check=True
    )
    assert "./server/index.js" in result.stdout and "./.openai/hosting.json" in result.stdout
    (tmp_path / "dist" / "private.txt").write_text("must not enter archive")
    rejected = subprocess.run(
        ["node", str(tmp_path / "scripts" / "build.mjs"), "--archive"], capture_output=True
    )
    assert rejected.returncode != 0 and b"Unexpected artifact file" in rejected.stderr


@pytest.mark.asyncio
async def test_backend_cli_sdk_registration_and_stop(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "overture_maps_mcp",
            "--transport",
            "streamable-http",
            "--sites-backend",
            "--port",
            str(port),
        ],
        env=os.environ
        | {
            "OVERTURE_BACKEND_TOKEN": TOKEN,
            "OVERTURE_MAPS_MCP_STORAGE_DIR": str(tmp_path / ".runtime"),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as probe:
            for _ in range(200):
                assert process.poll() is None, "Backend exited before readiness"
                try:
                    assert (await probe.post(url, json={})).status_code == 401
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            else:
                pytest.fail("Backend startup timed out")
        async with httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"}, trust_env=False
        ) as http:
            for mode in ["auto", "legacy"]:
                async with Client(
                    streamable_http_client(url, http_client=http), mode=mode
                ) as client:
                    assert len((await client.list_tools()).tools) == 9
                    result = await client.call_tool("overture_catalog", {"sql": "SELECT 1"})
                    assert result.is_error
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    assert process.poll() is not None
