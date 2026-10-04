from __future__ import annotations

import argparse
import logging
import sys

from overture_maps_mcp.storage import Storage, footprint


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Overture Maps MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="Loopback HTTP port (default: 8000)")
    args = parser.parse_args()
    try:
        from overture_maps_mcp.server import mcp
    except ModuleNotFoundError as exc:
        if exc.name == "mcp":
            parser.exit(1, "MCP support is optional: install overture-maps-mcp[mcp].\n")
        raise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    storage = Storage.default()
    with storage.lease():
        size = footprint(storage.root)["bytes"] / 1024**2
        print(
            f"MCP storage: {storage.root} ({size:.1f} MiB). Use manage.py storage/clean.",
            file=sys.stderr,
        )
        if args.transport == "stdio":
            mcp.run()
        else:
            mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
