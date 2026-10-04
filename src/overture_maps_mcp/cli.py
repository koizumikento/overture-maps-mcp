"""JSON command line interface; storage commands require only the standard library."""

from __future__ import annotations

import argparse
import inspect
import io
import json
import sys
from pathlib import Path
from typing import Any, Never

from overture_maps_mcp import _OPERATIONS, __version__
from overture_maps_mcp.storage import Storage, StorageError

MAX_INPUT_BYTES = 1_000_000


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        error(message, "INVALID_ARGUMENT")
        self.exit(2)


def json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected valid JSON.") from exc


def arguments_parser(query_commands: bool) -> argparse.ArgumentParser:
    parser = Parser(description="Overture Maps geographic search and analysis")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--storage-dir", help="Dedicated absolute .runtime directory")
    parser.add_argument("--pretty", action="store_true", help="Indent output JSON")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in _OPERATIONS:
        command = commands.add_parser(name.replace("_", "-"))
        command.add_argument("--params", help="JSON object, @UTF-8-file, or - for stdin")
        if not query_commands:
            continue
        from overture_maps_mcp.api import Client

        method = getattr(Client, name)
        command.description = inspect.getdoc(method)
        for key, parameter in inspect.signature(method).parameters.items():
            if key == "self":
                continue
            options: dict[str, Any] = {"dest": key, "default": argparse.SUPPRESS}
            flag = "--" + key.replace("_", "-")
            flags = [flag, "--type"] if key == "feature_type" else [flag]
            if key == "bounds":
                options.update(nargs=4, type=float, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
            elif key == "center":
                options.update(nargs=2, type=float, metavar=("LONGITUDE", "LATITUDE"))
            elif key in ("polygon", "left", "right"):
                options.update(type=json_value)
            elif key in ("filters", "aggregations"):
                flags = ["--filter" if key == "filters" else "--aggregation"]
                options.update(type=json_value, action="append")
            elif key in ("fields", "group_by"):
                options.update(nargs="+")
            elif isinstance(parameter.default, bool):
                options.update(action=argparse.BooleanOptionalAction)
            elif key == "limit":
                options.update(type=int)
            elif key in ("radius_m", "distance_m", "min_confidence"):
                options.update(type=float)
            command.add_argument(*flags, **options)
    commands.add_parser("storage", help="Show dedicated and excluded shared storage")
    commands.add_parser("clean", help="Delete only dedicated storage after active calls stop")
    return parser


def read_parameters(source: str | None) -> dict[str, Any]:
    if source is None:
        return {}
    if source == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    elif source.startswith("@"):
        with Path(source[1:]).open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    else:
        raw = source.encode("utf-8")
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("INPUT_TOO_LARGE: JSON input must be <= 1 MB.")
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("INVALID_INPUT: --params must contain a JSON object.")
    return data


def main(argv: list[str] | None = None) -> int:
    # The JSON and help contract is UTF-8 even on Windows with a legacy console codepage.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    names = {name.replace("_", "-") for name in _OPERATIONS}
    parser = arguments_parser(any(arg in names for arg in argv))
    args = parser.parse_args(argv)
    try:
        storage = Storage.for_runtime(args.storage_dir) if args.storage_dir else Storage.default()
        if args.command == "storage":
            result = storage.report(include_legacy=False)
        elif args.command == "clean":
            result = {"removed": storage.clean(include_legacy=False)}
        else:
            from pydantic import ValidationError

            from overture_maps_mcp.api import Client
            from overture_maps_mcp.models import QueryError

            params = read_parameters(args.params)
            flags = vars(args).copy()
            for key in ("command", "params", "storage_dir", "pretty"):
                flags.pop(key, None)
            if set(params) & set(flags):
                raise ValueError("DUPLICATE_ARGUMENT: use either --params or a flag for each key.")
            if "bounds" in flags:
                flags["bounds"] = dict(
                    zip(("west", "south", "east", "north"), flags["bounds"], strict=True)
                )
            if "center" in flags:
                flags["center"] = dict(zip(("longitude", "latitude"), flags["center"], strict=True))
            client = Client(storage_dir=storage.root)
            try:
                response = getattr(client, args.command.replace("-", "_"))(**(params | flags))
                result = response.model_dump(mode="json")
            except ValidationError as exc:
                details = "; ".join(
                    f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
                    for item in exc.errors(include_url=False)[:5]
                )
                raise ValueError(f"INVALID_ARGUMENT: {details}. Check --help.") from None
            except QueryError as exc:
                error(str(exc), "QUERY_ERROR")
                return 1
        print(
            json.dumps(
                result, ensure_ascii=False, allow_nan=False, indent=2 if args.pretty else None
            )
        )
        return 0
    except ValueError as exc:
        error(str(exc), "INVALID_INPUT")
        return 2
    except (StorageError, OSError) as exc:
        error(str(exc), "STORAGE_OR_IO_ERROR")
        return 1
    except KeyboardInterrupt:
        return 130


def error(message: str, code: str) -> None:
    print(
        json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
