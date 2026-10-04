from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from overture_maps_mcp.storage import Storage, StorageError, footprint


def directory_link(link: Path, destination: Path) -> None:
    if os.name == "nt":
        importlib.import_module("_winapi").CreateJunction(str(destination), str(link))
    else:
        link.symlink_to(destination, target_is_directory=True)


def test_owned_lifecycle_and_busy_cleanup(tmp_path):
    storage = Storage(tmp_path)
    source = tmp_path / "keep.py"
    source.write_text("source remains")
    assert storage.report()["owned"]["bytes"] == 0
    assert not storage.root.exists()
    with storage.lease():
        cache = storage.extension_directory() / "extension.bin"
        cache.write_bytes(b"x" * 100)
        assert storage.report()["components"]["duckdb"]["bytes"] == 100
        with pytest.raises(StorageError, match="STORAGE_BUSY"):
            storage.clean()
        assert cache.exists()
    assert storage.clean() == [str(storage.root)]
    assert source.read_text() == "source remains"
    assert not storage.root.exists()
    assert storage.clean() == []
    assert storage.report()["owned"]["bytes"] == 0


def test_unowned_storage_and_unknown_files_are_not_deleted(tmp_path):
    storage = Storage(tmp_path)
    storage.root.mkdir()
    data = storage.root / "important.txt"
    data.write_text("keep")
    with pytest.raises(StorageError, match="UNOWNED_STORAGE"):
        storage.initialize()
    with pytest.raises(StorageError, match="UNOWNED_STORAGE"):
        storage.clean()
    assert data.read_text() == "keep"
    data.unlink()
    storage.root.rmdir()
    storage.initialize()
    data.write_text("keep")
    with pytest.raises(StorageError, match="UNOWNED_FILES"):
        storage.clean()
    assert data.read_text() == "keep"


def test_junction_root_is_refused_and_nested_link_does_not_escape(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    data = outside / "important.txt"
    data.write_text("outside remains")
    storage = Storage(project)
    directory_link(storage.root, outside)
    with pytest.raises(StorageError, match="UNSAFE_PATH"):
        storage.clean()
    storage.root.unlink() if os.name != "nt" else storage.root.rmdir()
    storage.initialize()
    directory_link(storage.extension_directory() / "linked", outside)
    assert footprint(storage.root / "duckdb")["bytes"] == 0
    storage.clean()
    assert data.read_text() == "outside remains"


def test_live_child_and_crash_release(tmp_path):
    storage = Storage(tmp_path)
    code = (
        "import sys; from pathlib import Path; "
        "from overture_maps_mcp.storage import Storage; "
        "lease=Storage(Path(sys.argv[1])).lease(); lease.__enter__(); "
        "print('ready',flush=True); sys.stdin.readline()"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        with pytest.raises(StorageError, match="STORAGE_BUSY"):
            storage.clean()
    finally:
        process.terminate()
        process.wait(timeout=5)
    storage.clean()
    assert not storage.root.exists()


def test_legacy_preflight_preserves_unknown_data(tmp_path):
    storage = Storage(tmp_path)
    storage.initialize()
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("home = test")
    cache = tmp_path / ".cache"
    cache.mkdir()
    unexpected = cache / "important.txt"
    unexpected.write_text("keep")
    with pytest.raises(StorageError, match="UNOWNED_FILES"):
        storage.clean()
    assert storage.root.exists() and venv.exists() and unexpected.exists()
    unexpected.unlink()
    (cache / "uv").mkdir()
    (cache / "uv" / "blob").write_bytes(b"x")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / ".gitignore").write_text("*\n")
    (dist / "overture_maps_mcp-0.1.1.tar.gz").write_bytes(b"build")
    bytecode = tmp_path / "__pycache__"
    bytecode.mkdir()
    (bytecode / "generated.cpython-312.pyc").write_bytes(b"compiled")
    storage.clean(legacy_only=True)
    assert storage.root.exists() and not venv.exists() and not cache.exists() and not dist.exists()
    assert not bytecode.exists()


def test_manager_cleanup_does_not_need_installed_dependencies(tmp_path):
    project = Path(__file__).resolve().parents[1]
    shutil.copyfile(project / "manage.py", tmp_path / "manage.py")
    package = tmp_path / "src" / "overture_maps_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    shutil.copyfile(project / "src/overture_maps_mcp/storage.py", package / "storage.py")
    storage = Storage(tmp_path)
    storage.initialize()
    storage.extension_directory().joinpath("blob").write_bytes(b"x")
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(tmp_path / "manage.py"), "clean"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["removed"] == [str(storage.root)]
    assert not storage.root.exists()
    assert (tmp_path / "manage.py").exists()
