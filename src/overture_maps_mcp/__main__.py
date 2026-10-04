from __future__ import annotations

import argparse
import logging

from overture_maps_mcp.server import mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Overture Maps MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="Loopback HTTP port (default: 8000)")
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
