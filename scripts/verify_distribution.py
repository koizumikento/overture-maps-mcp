"""Run only against an isolated core wheel environment, without MCP or network queries."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from overture_maps_mcp import Client, __version__
from overture_maps_mcp.storage import Storage


def main() -> None:
    assert version("overture-maps-mcp") == __version__ == "0.3.0"
    assert importlib.util.find_spec("mcp") is None
    import overture_maps_mcp

    assert Path(overture_maps_mcp.__file__).with_name("py.typed").is_file()
    with TemporaryDirectory(dir=Path.cwd() / ".runtime" / "pytest") as directory:
        root = Path(directory) / "地理" / ".runtime"
        client = Client(storage_dir=root)
        assert client.storage()["owned"]["bytes"] == 0 and not root.exists()
        result = subprocess.run(
            ["overture-maps", "--storage-dir", str(root), "storage"],
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        assert json.loads(result.stdout)["owned"]["bytes"] == 0
        assert not result.stderr and not root.exists()
        for command in (
            "catalog",
            "schema",
            "search",
            "get-feature",
            "summarize",
            "nearest",
            "spatial-join",
            "compare-releases",
            "resolve-id",
        ):
            subprocess.run(["overture-maps", command, "--help"], capture_output=True, check=True)
        missing_extra = subprocess.run(
            [sys.executable, "-m", "overture_maps_mcp"],
            capture_output=True,
            text=True,
        )
        assert missing_extra.returncode == 1
        assert "install overture-maps-mcp[mcp]" in missing_extra.stderr
        assert "Traceback" not in missing_extra.stderr
        # Cleanup remains usable with no core dependencies installed (Python -S).
        package_parent = Path(overture_maps_mcp.__file__).parent.parent
        storage = Storage.for_runtime(root)
        storage.initialize()
        storage.extension_directory().joinpath("blob").write_bytes(b"test")
        code = (
            "import sys; sys.path.insert(0,sys.argv[1]); "
            "from overture_maps_mcp.cli import main; raise SystemExit(main(sys.argv[2:]))"
        )
        subprocess.run(
            [
                sys.executable,
                "-S",
                "-B",
                "-c",
                code,
                str(package_parent),
                "--storage-dir",
                str(root),
                "clean",
            ],
            capture_output=True,
            check=True,
        )
        assert not root.exists()
    print(
        json.dumps(
            {
                "version": __version__,
                "mcp_installed": False,
                "cli_commands": 9,
                "dependency_free_cleanup": True,
            }
        )
    )


if __name__ == "__main__":
    main()
