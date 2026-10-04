from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from overture_maps_mcp.catalog import Catalog
from overture_maps_mcp.models import TYPES, Bounds, Theme
from overture_maps_mcp.service import Service

RELEASE = "2026-09-23.1"
IDS = [f"00000000-0000-4000-8000-{n:012d}" for n in range(1, 5)]


class FixtureCatalog(Catalog):
    def __init__(self) -> None:
        super().__init__()
        self._latest = RELEASE
        self._releases = [RELEASE]
        self._expires = float("inf")

    def files(
        self, release: str, theme: Theme, feature_type: str, bounds: Bounds | None = None
    ) -> list[str]:
        return [f"theme={theme}/type={feature_type}"]


@pytest.fixture
def bounds() -> Bounds:
    return Bounds(west=139.74, south=35.67, east=139.78, north=35.70)


@pytest.fixture
def fixture_service(tmp_path: Path) -> Iterator[Service]:
    conn = duckdb.connect()
    conn.execute("LOAD spatial")
    paths = {}
    for theme, types in TYPES.items():
        for feature_type in types:
            file = tmp_path / f"{theme}-{feature_type}.parquet"
            conn.execute(
                "CREATE OR REPLACE TABLE fixture AS SELECT "
                "?::VARCHAR AS id, {'primary': 'Synthetic Cafe'} AS names, "
                "{'primary': 'cafe'} AS taxonomy, 'food_and_drink' AS basic_category, "
                "'residential' AS class, 'test' AS subtype, 0.9::DOUBLE AS confidence, "
                "'JP' AS country, 'open' AS operating_status, "
                "[{'dataset': 'synthetic-v1', 'license': 'CC0-1.0'}] AS sources, "
                "{'xmin': 139.75, 'xmax': 139.75, 'ymin': 35.68, 'ymax': 35.68} AS bbox, "
                "ST_Point(139.75,35.68) AS geometry",
                [IDS[0]],
            )
            conn.execute("INSERT INTO fixture SELECT * REPLACE (? AS id) FROM fixture", [IDS[1]])
            conn.execute(
                "INSERT INTO fixture SELECT * REPLACE "
                "(? AS id, NULL AS basic_category, 0.3 AS confidence) FROM fixture LIMIT 1",
                [IDS[2]],
            )
            # bbox overlaps the request but actual geometry does not; prune it precisely.
            conn.execute(
                "INSERT INTO fixture SELECT * REPLACE "
                "(? AS id, ST_Point(139.80,35.68) AS geometry, "
                "{'xmin':139.77,'xmax':139.81,'ymin':35.68,'ymax':35.68} AS bbox) "
                "FROM fixture LIMIT 1",
                [IDS[3]],
            )
            if theme != "places":
                conn.execute("ALTER TABLE fixture DROP COLUMN taxonomy")
                conn.execute("ALTER TABLE fixture DROP COLUMN confidence")
            if theme == "addresses":
                conn.execute("ALTER TABLE fixture DROP COLUMN names")
            conn.execute("COPY fixture TO ? (FORMAT PARQUET)", [str(file)])
            paths[f"theme={theme}/type={feature_type}"] = str(file)

    def local_query(sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
        path = next(value for key, value in paths.items() if key in parameters[0][0])
        result = conn.execute(sql, [path, *parameters[1:]])
        names = [col[0] for col in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]

    yield Service(FixtureCatalog(), local_query)
    conn.close()
