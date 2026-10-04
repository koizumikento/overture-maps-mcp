from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from overture_maps_mcp.models import Bounds, QueryError
from overture_maps_mcp.storage import Storage, StorageError

Query = Callable[[str, list[Any]], list[dict[str, Any]]]
MAX_RESPONSE_BYTES = 400_000
_gate = threading.BoundedSemaphore(2)


@contextmanager
def connection(storage: Storage | None = None):
    """Two bounded in-process queries; cancel database work after 30 seconds."""
    if not _gate.acquire(timeout=1):
        raise QueryError("BUSY: two queries are running; retry after they finish.")
    conn = None
    timer = None
    lease = None
    try:
        storage = storage or Storage.default()
        lease = storage.lease()
        lease.__enter__()
        conn = duckdb.connect(
            config={
                "memory_limit": "512MB",
                "threads": "2",
                "temp_directory": "",
                "extension_directory": str(storage.extension_directory()),
            }
        )
        timer = threading.Timer(30, conn.interrupt)
        timer.daemon = True
        timer.start()
        # Trusted fixed extensions only; installed/cached by DuckDB on first use.
        conn.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        conn.execute("SET s3_region = 'us-west-2'; SET http_timeout = 10; SET http_retries = 1")
        yield conn
    except StorageError as exc:
        raise QueryError(str(exc)) from exc
    except duckdb.Error as exc:
        raise QueryError(
            "QUERY_FAILED: data, extension or schema unavailable, or time/memory limit exceeded. "
            "Retry with a smaller area, verify release with overture_catalog, "
            "and inspect columns with overture_schema."
        ) from exc
    finally:
        if timer:
            timer.cancel()
            timer.join()
        if conn:
            conn.close()
        if lease:
            lease.__exit__(None, None, None)
        _gate.release()


def execute(
    sql: str, parameters: list[Any], *, storage: Storage | None = None
) -> list[dict[str, Any]]:
    with connection(storage) as conn:
        result = conn.execute(sql, parameters)
        names = [col[0] for col in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def bounded(data: dict[str, Any]) -> dict[str, Any]:
    def encode(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, date | datetime):
            return value.isoformat()
        raise QueryError("UNSUPPORTED_VALUE: this dataset contains an unsupported property type.")

    try:
        serialized = json.dumps(data, ensure_ascii=False, default=encode, allow_nan=False)
    except ValueError as exc:
        raise QueryError("UNSUPPORTED_VALUE: dataset contains a non-finite numeric value.") from exc
    if len(serialized.encode()) > MAX_RESPONSE_BYTES:
        raise QueryError(
            "RESULT_TOO_LARGE: lower limit, select fewer fields, narrow area, or omit geometry."
        )
    return json.loads(serialized)


def bbox_filter(bounds: Bounds) -> tuple[str, list[Any]]:
    # Bounding boxes prune parquet row groups; geometry then removes false positives.
    sql = (
        "bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ? "
        "AND ST_Intersects(geometry, ST_MakeEnvelope(?, ?, ?, ?))"
    )
    return sql, [
        bounds.east,
        bounds.west,
        bounds.north,
        bounds.south,
        bounds.west,
        bounds.south,
        bounds.east,
        bounds.north,
    ]


def cursor_key(release: str, theme: str, feature_type: str, scope: dict[str, Any]) -> str:
    raw = json.dumps([release, theme, feature_type, scope], sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()
