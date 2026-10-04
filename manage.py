"""Dependency-free manager; run with uv --no-project so cleanup can remove the venv."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "src"))

from overture_maps_mcp.storage import Storage, StorageError, footprint  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage only this MCP's local generated storage")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("storage", help="Show owned, legacy and excluded shared file sizes")
    clean = actions.add_parser("clean", help="Remove owned generated files; stop the MCP first")
    clean.add_argument(
        "--legacy-only", action="store_true", help="Keep .runtime; remove old repo artifacts"
    )
    setup = actions.add_parser("setup", help="Install into .runtime")
    setup.add_argument("--dev", action="store_true", help="Include development tools")
    run = actions.add_parser("run", help="Start the MCP with dedicated venv/cache/extensions")
    run.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    run.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    storage = Storage(PROJECT)
    try:
        if args.action == "storage":
            print(json.dumps(storage.report(), ensure_ascii=False, indent=2))
        elif args.action == "clean":
            candidates = [PROJECT / name for name in (".venv", ".cache", "dist")]
            if not args.legacy_only:
                candidates.append(storage.root)
            if any(Path(sys.prefix).resolve().is_relative_to(p.resolve()) for p in candidates):
                raise StorageError(
                    "EXTERNAL_PYTHON_REQUIRED: use uv run --isolated --no-project --no-cache "
                    "--python 3.12 python -B manage.py clean"
                )
            print(json.dumps({"removed": storage.clean(args.legacy_only)}, indent=2))
        else:
            with storage.lease():
                env = storage.environment()
                if args.action == "setup":
                    command = ["uv", "sync", "--frozen"]
                    if not args.dev:
                        command.append("--no-dev")
                else:
                    size = footprint(storage.root)["bytes"] / 1024**2
                    print(
                        f"Storage: {storage.root} ({size:.1f} MiB before startup)", file=sys.stderr
                    )
                    command = [
                        "uv",
                        "run",
                        "--frozen",
                        "--no-dev",
                        "overture-maps-mcp",
                        "--transport",
                        args.transport,
                        "--port",
                        str(args.port),
                    ]
                return subprocess.call(command, cwd=PROJECT, env=env)
        return 0
    except (StorageError, OSError) as exc:
        print(
            f"{exc}\nNo shared DuckDB files were removed. Stop active processes and retry.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
