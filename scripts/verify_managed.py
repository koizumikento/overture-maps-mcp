"""Explicit public-data check through the manager, including active cleanup refusal."""

import asyncio
import json
import subprocess
from pathlib import Path

from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def main() -> None:
    project = Path(__file__).resolve().parents[1]
    manager = [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--no-cache",
        "--python",
        "3.12",
        "python",
        "-B",
        str(project / "manage.py"),
    ]
    async with Client(
        StdioServerParameters(command=manager[0], args=[*manager[1:], "run"])
    ) as client:
        assert len((await client.list_tools()).tools) == 5
        catalog = await client.call_tool("overture_catalog", {})
        assert not catalog.is_error and catalog.structured_content is not None
        release = catalog.structured_content["release"]
        result = await client.call_tool(
            "overture_search",
            {
                "theme": "places",
                "feature_type": "place",
                "release": release,
                "bounds": {"west": 139.760, "south": 35.680, "east": 139.762, "north": 35.682},
                "limit": 1,
            },
        )
        assert not result.is_error and result.structured_content is not None
        assert result.structured_content["data"]["returned"] == 1
        cleaned = subprocess.run([*manager, "clean"], capture_output=True, text=True)
        assert cleaned.returncode == 1 and "STORAGE_BUSY" in cleaned.stderr, cleaned.stderr
        print(
            json.dumps(
                {
                    "managed_transport": "stdio",
                    "protocol": client.protocol_version,
                    "release": release,
                    "places_returned": 1,
                    "active_cleanup": "refused",
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
