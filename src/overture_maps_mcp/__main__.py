from __future__ import annotations

import argparse
import logging
import os
import sys

from overture_maps_mcp.storage import Storage, footprint


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Overture Maps MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="Loopback HTTP port (default: 8000)")
    parser.add_argument(
        "--sites-backend", action="store_true", help="Authenticated loopback backend"
    )
    args = parser.parse_args()
    if args.sites_backend and args.transport != "streamable-http":
        parser.error("--sites-backend requires --transport streamable-http")
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
        elif args.sites_backend:
            import uvicorn

            from overture_maps_mcp.backend import create_backend

            try:
                app = create_backend(os.environ.get("OVERTURE_BACKEND_TOKEN", ""))
            except ValueError as exc:
                parser.exit(1, f"{exc}\n")
            uvicorn.run(
                app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False
            )
        else:
            mcp.run(transport="streamable-http", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
