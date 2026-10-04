"""Explicit loopback transport check; owns and stops its test server process."""

import asyncio
import json
import socket
import subprocess
import sys

import httpx
from mcp import Client


async def main() -> None:
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
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as probe:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("HTTP server exited during startup")
                try:
                    await probe.get(url)
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("HTTP server did not start")
        async with Client(url) as client:
            assert len((await client.list_tools()).tools) == 5
            result = await client.call_tool("overture_catalog", {})
            assert not result.is_error and result.structured_content is not None
            print(
                json.dumps(
                    {
                        "transport": "streamable-http",
                        "protocol": client.protocol_version,
                        "tools": 5,
                        "release": result.structured_content["release"],
                    }
                )
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    asyncio.run(main())
