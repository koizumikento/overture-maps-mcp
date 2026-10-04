"""Opt-in public read-only acceptance; log counts/keys, never save map datasets."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from time import monotonic
from typing import cast

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from overture_maps_mcp.models import TYPES, Bounds, Theme
from overture_maps_mcp.query import execute
from overture_maps_mcp.service import Service


async def main(analysis_only: bool = False) -> None:
    sampler = Service()
    transport = StdioServerParameters(command=sys.executable, args=["-m", "overture_maps_mcp"])
    async with Client(transport) as client:
        tools = (await client.list_tools()).tools
        assert len(tools) == 9

        async def call(tool, params):
            result = await asyncio.wait_for(client.call_tool(tool, params), timeout=120)
            assert not result.is_error, (tool, result.content)
            assert result.structured_content is not None
            return result.structured_content

        catalog = await call("overture_catalog", {})
        release = catalog["release"]
        print(
            json.dumps(
                {
                    "release": release,
                    "protocol": client.protocol_version,
                    "tools": [t.name for t in tools],
                }
            ),
            flush=True,
        )
        for theme, kinds in [] if analysis_only else TYPES.items():
            for kind in kinds:
                started = monotonic()
                schema = await call(
                    "overture_schema", {"theme": theme, "feature_type": kind, "release": release}
                )
                files = sampler.catalog.files(release, cast(Theme, theme), kind)
                sample = execute(
                    "SELECT id, ST_X(ST_PointOnSurface(geometry)) AS longitude, "
                    "ST_Y(ST_PointOnSurface(geometry)) AS latitude FROM read_parquet(?) LIMIT 1",
                    [files],
                )[0]
                lon, lat = sample["longitude"], sample["latitude"]
                bounds = Bounds(
                    west=max(-180, lon - 0.0001),
                    east=min(180, lon + 0.0001),
                    south=max(-90, lat - 0.0001),
                    north=min(90, lat + 0.0001),
                )
                params = {
                    "theme": theme,
                    "feature_type": kind,
                    "release": release,
                    "bounds": bounds.model_dump(),
                    "limit": 1,
                    "filters": [{"field": "id", "op": "eq", "value": sample["id"]}],
                }
                result = await call("overture_search", params)
                features = result["data"]["features"]
                assert len(features) == 1 and features[0]["id"] == sample["id"]
                expected = set(schema["data"]["columns"]) - {"geometry"}
                assert expected == set(features[0])
                summary = await call(
                    "overture_summarize", {k: v for k, v in params.items() if k != "limit"}
                )
                assert summary["data"]["total"] == 1
                detail = await call(
                    "overture_get_feature",
                    {
                        "theme": theme,
                        "feature_type": kind,
                        "release": release,
                        "bounds": bounds.model_dump(),
                        "identifier": sample["id"],
                        "include_geometry": False,
                    },
                )
                assert detail["data"]["returned"] == 1
                assert set(detail["data"]["features"][0]) == expected
                print(
                    json.dumps(
                        {
                            "theme": theme,
                            "type": kind,
                            "columns": len(expected),
                            "search": 1,
                            "filtered_count": 1,
                            "detail": 1,
                            "seconds": round(monotonic() - started, 2),
                        }
                    ),
                    flush=True,
                )

        bounds = {"west": 139.760, "south": 35.680, "east": 139.762, "north": 35.682}
        center = {"longitude": 139.761, "latitude": 35.681}
        nearest = await call(
            "overture_nearest",
            {
                "theme": "places",
                "feature_type": "place",
                "release": release,
                "center": center,
                "radius_m": 200,
                "limit": 2,
            },
        )
        assert nearest["data"]["returned"] > 0
        distances = [f["distance_m"] for f in nearest["data"]["features"]]
        assert distances == sorted(distances) and max(distances) <= 200
        identifier = nearest["data"]["features"][0]["id"]
        resolved = await call("overture_resolve_id", {"identifier": identifier})
        assert resolved["data"]["status"] == "live"
        assert resolved["data"]["feature"]["id"] == identifier
        polygon = {
            "type": "Polygon",
            "coordinates": [
                [
                    [139.760, 35.680],
                    [139.762, 35.680],
                    [139.762, 35.682],
                    [139.760, 35.682],
                    [139.760, 35.680],
                ]
            ],
        }
        rectangle = await call(
            "overture_summarize",
            {"theme": "places", "feature_type": "place", "bounds": bounds, "release": release},
        )
        polygon_count = await call(
            "overture_summarize",
            {"theme": "places", "feature_type": "place", "polygon": polygon, "release": release},
        )
        assert rectangle["data"]["total"] == polygon_count["data"]["total"]
        for theme, kind, field in (
            ("buildings", "building", "@area_m2"),
            ("transportation", "segment", "@length_m"),
        ):
            metrics = await call(
                "overture_summarize",
                {
                    "theme": theme,
                    "feature_type": kind,
                    "bounds": bounds,
                    "release": release,
                    "aggregations": [{"field": field, "op": "sum", "label": "total"}],
                },
            )
            assert metrics["data"]["metrics"]["total"] > 0
            print(
                json.dumps({"metric": field, "value": metrics["data"]["metrics"]["total"]}),
                flush=True,
            )
        join = await call(
            "overture_spatial_join",
            {
                "left": {"theme": "places", "feature_type": "place", "fields": ["names.primary"]},
                "right": {"theme": "divisions", "feature_type": "division_area", "fields": ["id"]},
                "bounds": bounds,
                "release": release,
                "relation": "within",
                "mode": "count",
            },
        )
        assert join["data"]["total_pairs"] > 0
        older = [r for r in catalog["data"]["available_releases"] if r < release][-1]
        comparison = await call(
            "overture_compare_releases",
            {
                "theme": "places",
                "feature_type": "place",
                "bounds": bounds,
                "before": older,
                "after": release,
                "limit": 2,
                "fields": ["names.primary"],
            },
        )
        counts = comparison["data"]["counts"]
        assert (
            counts["added"] + counts["modified"] + counts["unchanged"]
            == comparison["data"]["after_count"]
        )
        print(
            json.dumps(
                {
                    "nearest_distances_m": distances,
                    "resolve": "live",
                    "polygon_count": polygon_count["data"]["total"],
                    "join_pairs": join["data"]["total_pairs"],
                    "comparison": {"before": older, "after": release, "counts": counts},
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--analysis-only",
        action="store_true",
        help="Skip the 15-type matrix; recheck spatial/ID/release operations",
    )
    asyncio.run(main(parser.parse_args().analysis_only))
