"""Owned local storage and cleanup. Uses only Python's standard library."""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

OWNER = {"application": "overture-maps-mcp", "format": 1}
COMPONENTS = ("venv", "uv-cache", "duckdb", "pycache", "pytest", "ruff", ".leases")
LEGACY = (
    ".venv",
    ".cache",
    "dist",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "src/overture_maps_mcp/__pycache__",
    "tests/__pycache__",
    "scripts/__pycache__",
)


class StorageError(Exception):
    """Local storage is busy, inaccessible or outside the owned boundary."""


def linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


@contextmanager
def file_lock(path: Path, timeout: float = 1):
    if linked(path):
        raise StorageError(f"UNSAFE_PATH: lock is a link: {path}")
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise StorageError(
                        "STORAGE_BUSY: stop the MCP/other management command first."
                    ) from exc
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def footprint(path: Path) -> dict[str, int | float]:
    """Logical file sizes; never follow directory junctions or symlinks."""
    if linked(path):
        raise StorageError(f"UNSAFE_PATH: storage is a link: {path}")
    if not path.exists():
        return {"bytes": 0, "mib": 0.0, "files": 0}
    total = files = 0

    def fail(error: OSError) -> None:
        raise StorageError(f"STORAGE_UNREADABLE: {error.filename}") from error

    for directory, dirs, names in os.walk(path, followlinks=False, onerror=fail):
        base = Path(directory)
        dirs[:] = [name for name in dirs if not linked(base / name)]
        for name in names:
            file = base / name
            if not linked(file):
                try:
                    total += file.stat().st_size
                    files += 1
                except FileNotFoundError:
                    pass  # Snapshot: an active process may remove its lease/cache entry.
    return {"bytes": total, "mib": round(total / 1024**2, 2), "files": files}


class Storage:
    def __init__(self, project: Path) -> None:
        self.project = project.resolve(strict=True)
        self.root = self.project / ".runtime"
        self.guard = self.project / ".runtime.lock"

    @classmethod
    def default(cls) -> Storage:
        configured = os.environ.get("OVERTURE_MAPS_MCP_STORAGE_DIR")
        if configured:
            root = Path(configured).absolute()
            if root.name != ".runtime" or linked(root):
                raise StorageError(
                    "UNSAFE_PATH: configured storage must be an unlinked .runtime directory."
                )
            return cls(root.parent)
        return cls(Path.cwd())

    def validate(self) -> None:
        if linked(self.root) or self.root.resolve() != self.project / ".runtime":
            raise StorageError("UNSAFE_PATH: .runtime must be inside this repository.")
        if self.root.exists():
            marker = self.root / ".owner.json"
            if linked(marker) or not marker.is_file():
                raise StorageError("UNOWNED_STORAGE: .runtime has no valid ownership marker.")
            try:
                if json.loads(marker.read_text(encoding="utf-8")) != OWNER:
                    raise ValueError
            except (ValueError, OSError) as exc:
                raise StorageError(
                    "UNOWNED_STORAGE: .runtime has no valid ownership marker."
                ) from exc
            unknown = set(p.name for p in self.root.iterdir()) - {*COMPONENTS, ".owner.json"}
            if unknown:
                raise StorageError(
                    "UNOWNED_FILES: .runtime contains unexpected files; review them first."
                )
            for component in COMPONENTS:
                if linked(self.root / component):
                    raise StorageError(f"UNSAFE_PATH: .runtime/{component} is a link.")

    def _ensure(self) -> None:
        self.validate()
        if not self.root.exists():
            self.root.mkdir()
            (self.root / ".owner.json").write_text(json.dumps(OWNER), encoding="utf-8")

    def initialize(self) -> None:
        with file_lock(self.guard):
            self._ensure()

    @contextmanager
    def lease(self):
        """Crash-safe OS locks protect live servers without relying on stale PIDs."""
        with file_lock(self.guard):
            self._ensure()
            leases = self.root / ".leases"
            leases.mkdir(exist_ok=True)
            path = leases / uuid4().hex
            lock = file_lock(path)
            lock.__enter__()
        try:
            yield
        finally:
            lock.__exit__(None, None, None)
            path.unlink(missing_ok=True)

    def extension_directory(self) -> Path:
        self.initialize()
        path = self.root / "duckdb"
        path.mkdir(exist_ok=True)
        return path

    def environment(self) -> dict[str, str]:
        environment = os.environ | {
            "OVERTURE_MAPS_MCP_STORAGE_DIR": str(self.root),
            "UV_PROJECT_ENVIRONMENT": str(self.root / "venv"),
            "UV_CACHE_DIR": str(self.root / "uv-cache"),
            "UV_LINK_MODE": "copy",
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
        }
        environment.pop("VIRTUAL_ENV", None)
        return environment

    def report(self) -> dict[str, Any]:
        self.validate()
        owned = footprint(self.root)
        shared = Path.home() / ".duckdb" / "extensions"
        return {
            "root": str(self.root),
            "size_kind": "logical_file_bytes; hardlinks may be counted more than once",
            "owned": owned,
            "components": {name: footprint(self.root / name) for name in COMPONENTS},
            "legacy_in_repository": {name: footprint(self.project / name) for name in LEGACY},
            "shared_duckdb_extensions_excluded_from_cleanup": {
                "path": str(shared),
                **footprint(shared),
            },
        }

    def _legacy_targets(self) -> list[Path]:
        result = []
        for name in LEGACY:
            path = self.project / name
            if linked(path) or path.resolve() != self.project / name:
                raise StorageError(f"UNSAFE_PATH: {name} is outside the repository or is a link.")
            if not path.exists():
                continue
            if not path.is_dir():
                raise StorageError(f"UNOWNED_FILES: {name} is not a generated directory.")
            if name == ".venv" and not (path / "pyvenv.cfg").is_file():
                raise StorageError("UNOWNED_FILES: .venv is not a recognizable Python environment.")
            if name == ".cache" and set(p.name for p in path.iterdir()) - {
                "uv",
                "live-validation.jsonl",
            }:
                raise StorageError("UNOWNED_FILES: legacy .cache contains unexpected files.")
            if name == "dist" and any(
                not p.is_file()
                or (
                    not (p.name == ".gitignore" and p.read_text(encoding="utf-8").strip() == "*")
                    and (
                        not p.name.startswith("overture_maps_mcp-")
                        or not p.name.endswith((".whl", ".tar.gz"))
                    )
                )
                for p in path.iterdir()
            ):
                raise StorageError("UNOWNED_FILES: dist contains unexpected files.")
            if name in {".pytest_cache", ".ruff_cache"} and not (path / "CACHEDIR.TAG").is_file():
                raise StorageError(f"UNOWNED_FILES: {name} is not a recognizable cache.")
            if name.endswith("__pycache__") and any(
                not p.is_file() or p.suffix != ".pyc" for p in path.iterdir()
            ):
                raise StorageError(f"UNOWNED_FILES: {name} contains unexpected files.")
            result.append(path)
        return result

    def clean(self, legacy_only: bool = False) -> list[str]:
        with file_lock(self.guard):
            self.validate()
            leases = self.root / ".leases"
            if leases.exists():
                for path in leases.iterdir():
                    with file_lock(path, timeout=0):
                        pass  # Dead processes release their locks automatically.
            targets = self._legacy_targets()
            if self.root.exists() and not legacy_only:
                targets.insert(0, self.root)
            # All absolute targets have been checked before the first recursive removal.
            for path in targets:
                shutil.rmtree(path)
            return [str(path) for path in targets]
