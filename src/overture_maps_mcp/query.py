from __future__ import annotations

import base64
import hashlib
import json
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import duckdb

from overture_maps_mcp.models import Bounds, QueryError
from overture_maps_mcp.storage import Storage, StorageError

Query = Callable[[str, list[Any]], list[dict[str, Any]]]
MAX_RESPONSE_BYTES = 400_000
_gate = threading.BoundedSemaphore(2)


@contextmanager
def connection():
    """Two bounded in-process queries; cancel database work after 30 seconds."""
    if not _gate.acquire(timeout=1):
        raise QueryError("BUSY: two queries are running; retry after they finish.")
    conn = None
    timer = None
    lease = None
    try:
        storage = Storage.default()
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


def execute(sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
    with connection() as conn:
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
        raise QueryError("RESULT_TOO_LARGE: lower limit, narrow the area, or omit geometry.")
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


def encode_cursor(identifier: str, key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([identifier, key]).encode()).decode()


def decode_cursor(cursor: str, key: str) -> str:
    try:
        if len(cursor) > 1024:
            raise ValueError
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(payload, list) or len(payload) != 2 or payload[1] != key:
            raise ValueError
        return str(UUID(payload[0]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise QueryError(
            "INVALID_CURSOR: reuse the cursor with the same release, bounds and filters; "
            "restart without a cursor if any change."
        ) from exc
