from __future__ import annotations

import json

import duckdb
import pytest
from jsonschema import validate
from mcp import Client
from mcp.types import TextContent
from pydantic import ValidationError

from overture_maps_mcp import server
from overture_maps_mcp.analysis import circle_bounds
from overture_maps_mcp.catalog import Catalog
from overture_maps_mcp.models import (
    TYPES,
    Aggregation,
    AttributeFilter,
    Bounds,
    Dataset,
    Point,
    Polygon,
    QueryError,
)
from overture_maps_mcp.service import REGISTRY, Service
from overture_maps_mcp.storage import Storage

BEFORE = "2026-09-23.0"
AFTER = "2026-09-23.1"
CENTER = Point(longitude=139.75, latitude=35.68)
AREA = Bounds(west=139.74, east=139.78, south=35.67, north=35.70)


def uid(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


@pytest.fixture
def analysis_service(tmp_path, monkeypatch):
    """Immutable tiny snapshots with points, lines, areas and theme-specific properties."""
    conn = duckdb.connect(
        config={"extension_directory": str(Storage.default().extension_directory())}
    )
    conn.execute("LOAD spatial")
    paths = {}
    catalog = Catalog()
    catalog._latest, catalog._releases, catalog._expires = AFTER, [BEFORE, AFTER], float("inf")
    for release in (BEFORE, AFTER):
        for theme, kinds in TYPES.items():
            for kind in kinds:
                token = f"{release}/theme={theme}/type={kind}"
                file = tmp_path / f"{release}-{kind}.parquet"
                if kind in {
                    "building",
                    "building_part",
                    "division_area",
                    "land",
                    "land_cover",
                    "land_use",
                    "water",
                    "bathymetry",
                }:
                    geometries = [
                        "POLYGON((139.7495 35.6795,139.7525 35.6795,139.7525 35.6805,"
                        "139.7495 35.6805,139.7495 35.6795))",
                        "POLYGON((139.760 35.685,139.761 35.685,139.761 35.686,"
                        "139.760 35.686,139.760 35.685))",
                    ]
                    numbers = [101, 102]
                elif kind in {"segment", "division_boundary"}:
                    geometries = [
                        "LINESTRING(139.749 35.680,139.757 35.680)",
                        "LINESTRING(139.750 35.685,139.757 35.685)",
                    ]
                    numbers = [201, 202]
                else:
                    numbers = [1, 2, 3] if release == BEFORE else [1, 2, 4]
                    geometries = [
                        f"POINT({139.75 + {1: 0, 2: 0.001, 3: 0.005, 4: 0.004}[n]} 35.68)"
                        for n in numbers
                    ]
                conn.execute(
                    "CREATE OR REPLACE TABLE raw(id VARCHAR, name VARCHAR, height DOUBLE, "
                    "confidence DOUBLE, wkt VARCHAR)"
                )
                for n, wkt in zip(numbers, geometries, strict=True):
                    name = "Cafe Updated" if n == 1 and release == AFTER else "Cafe"
                    conn.execute(
                        "INSERT INTO raw VALUES (?,?,?,?,?)",
                        [uid(n), name, n * 10.0, 0.95 if n == 1 else 0.3 if n == 2 else 0.7, wkt],
                    )
                source_struct = (
                    "[{'license':'CC0-1.0','dataset':'synthetic-v2'}]"
                    if release == AFTER
                    else "[{'dataset':'synthetic-v2','license':'CC0-1.0'}]"
                )
                conn.execute(
                    "CREATE OR REPLACE TABLE features AS SELECT id, 1 AS version, "
                    "{'primary':name} AS names, {'primary':'cafe'} AS taxonomy, "
                    "'food_and_drink' AS basic_category, height, confidence, "
                    "'residential' AS class, 'JP' AS country, "
                    f"{source_struct} AS sources, "
                    "['one','two'] AS tags, NULL::VARCHAR AS nullable, "
                    "ST_GeomFromText(wkt) AS geometry FROM raw"
                )
                if kind == "address":
                    conn.execute(
                        "ALTER TABLE features ADD COLUMN postcode VARCHAR DEFAULT '111-1111'"
                    )
                    conn.execute(
                        "ALTER TABLE features ADD COLUMN street VARCHAR DEFAULT 'Synthetic Street'"
                    )
                if kind in {"building", "building_part"}:
                    conn.execute(
                        "ALTER TABLE features ADD COLUMN roof_shape VARCHAR DEFAULT 'gable'"
                    )
                if kind == "division_area":
                    conn.execute("ALTER TABLE features ADD COLUMN admin_level INTEGER DEFAULT 4")
                if release == AFTER and kind == "place":
                    conn.execute("ALTER TABLE features ADD COLUMN extra_note VARCHAR")
                conn.execute(
                    "ALTER TABLE features ADD COLUMN bbox "
                    "STRUCT(xmin DOUBLE,xmax DOUBLE,ymin DOUBLE,ymax DOUBLE)"
                )
                conn.execute(
                    "UPDATE features SET bbox={'xmin':ST_XMin(geometry),"
                    "'xmax':ST_XMax(geometry),'ymin':ST_YMin(geometry),'ymax':ST_YMax(geometry)}"
                )
                conn.execute("COPY features TO ? (FORMAT PARQUET)", [str(file)])
                paths[token] = str(file)

    def files(release, theme, kind, bounds=None):
        return [f"s3://fixture/{release}/theme={theme}/type={kind}/part-fixture.parquet"]

    monkeypatch.setattr(catalog, "files", files)

    def query(sql, parameters):
        if parameters and parameters[0] == REGISTRY:
            identifier = parameters[1]
            if identifier not in {uid(1), uid(3)}:
                return []
            return [
                {
                    "id": identifier,
                    "version": 1,
                    "first_seen": BEFORE,
                    "last_seen": AFTER if identifier == uid(1) else BEFORE,
                    "last_changed": AFTER,
                    "path": "theme=places/type=place/part-fixture.parquet"
                    if identifier == uid(1)
                    else None,
                    "bbox": {"xmin": 139.75, "xmax": 139.75, "ymin": 35.68, "ymax": 35.68}
                    if identifier == uid(1)
                    else None,
                }
            ]

        def substitute(value):
            if isinstance(value, list):
                return [substitute(item) for item in value]
            if isinstance(value, str):
                return next(
                    (path for key, path in paths.items() if value == key or key + "/" in value),
                    value,
                )
            return value

        try:
            result = conn.execute(sql, [substitute(p) for p in parameters])
        except duckdb.Error as exc:
            raise QueryError("QUERY_FAILED: fixture query failed") from exc
        return [
            dict(zip([c[0] for c in result.description], row, strict=True))
            for row in result.fetchall()
        ]

    yield Service(catalog, query)
    conn.close()


def test_full_properties_nested_projection_and_shared_conditions(analysis_service):
    service = analysis_service
    full = service.search("buildings", "building", AREA).data["features"][0]
    assert full["roof_shape"] == "gable"  # previously omitted by fixed DETAIL_FIELDS
    filters = [
        AttributeFilter(field="sources[].dataset", op="contains", value="synthetic-v2"),
        AttributeFilter(field="confidence", op="gte", value=0.8),
    ]
    result = service.search("places", "place", AREA, filters=filters, fields=["names.primary"])
    assert result.data["features"] == [
        {
            "names.primary": "Cafe Updated",
            "id": uid(1),
            "sources": [{"dataset": "synthetic-v2", "license": "CC0-1.0"}],
        }
    ]
    assert service.summarize("places", "place", AREA, filters=filters).data["total"] == 1
    assert (
        service.summarize(
            "places", "place", AREA, name="UPDATED", category="cafe", min_confidence=0.8
        ).data["total"]
        == 1
    )
    address = service.search(
        "addresses",
        "address",
        AREA,
        filters=[AttributeFilter(field="postcode", op="eq", value="111-1111")],
    )
    assert address.data["returned"] == 3


@pytest.mark.parametrize(
    "field,op,value,count",
    [
        ("height", "eq", 10, 1),
        ("height", "ne", 10, 2),
        ("height", "in", [10, 20], 2),
        ("height", "not_in", [10, 20], 1),
        ("height", "gt", 20, 1),
        ("height", "gte", 20, 2),
        ("height", "lt", 20, 1),
        ("height", "lte", 20, 2),
        ("height", "between", [10, 20], 2),
        ("names.primary", "contains", "UPDATED", 1),
        ("names.primary", "starts_with", "Cafe", 3),
        ("nullable", "is_null", None, 3),
        ("nullable", "not_null", None, 0),
        ("tags", "contains", "one", 3),
        ("tags", "contains_any", ["missing", "one"], 3),
        ("tags", "contains_all", ["one", "missing"], 0),
    ],
)
def test_filter_operators(analysis_service, field, op, value, count):
    conditions = [AttributeFilter(field=field, op=op, value=value)]
    assert (
        analysis_service.search("places", "place", AREA, filters=conditions).data["returned"]
        == count
    )
    assert (
        analysis_service.summarize("places", "place", AREA, filters=conditions).data["total"]
        == count
    )


def test_circle_nearest_distance_page_and_geojson(analysis_service):
    service = analysis_service
    first = service.search(
        "places", "place", center=CENTER, radius_m=500, sort_by="distance", limit=1
    )
    assert first.data["features"][0]["id"] == uid(1)
    assert first.data["features"][0]["distance_m"] == pytest.approx(0, abs=0.001)
    second = service.search(
        "places",
        "place",
        center=CENTER,
        radius_m=500,
        sort_by="distance",
        limit=1,
        cursor=first.next_cursor,
    )
    assert second.data["features"][0]["id"] == uid(2)
    assert 89 < second.data["features"][0]["distance_m"] < 92
    assert service.search("places", "place", center=CENTER, radius_m=100).data["returned"] == 2
    assert service.summarize("places", "place", center=CENTER, radius_m=100).data["total"] == 2
    assert service.search("buildings", "building", center=CENTER, radius_m=1).data["returned"] == 1
    assert (
        service.search("transportation", "segment", center=CENTER, radius_m=1).data["returned"] == 1
    )
    exported = service.search("places", "place", AREA, limit=1, output_format="geojson")
    fc = exported.data["feature_collection"]
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1
    assert fc["features"][0]["geometry"]["coordinates"] == [139.75, 35.68]
    assert "sources" in fc["features"][0]["properties"]
    assert exported.next_cursor
    with pytest.raises(QueryError, match="INVALID_CURSOR"):
        service.search(
            "places",
            "place",
            center=CENTER,
            radius_m=100,
            sort_by="distance",
            cursor=first.next_cursor,
        )


def polygon_with_hole():
    return Polygon(
        coordinates=[
            [
                [139.749, 35.679],
                [139.756, 35.679],
                [139.756, 35.681],
                [139.749, 35.681],
                [139.749, 35.679],
            ],
            [
                [139.7505, 35.6795],
                [139.7515, 35.6795],
                [139.7515, 35.6805],
                [139.7505, 35.6805],
                [139.7505, 35.6795],
            ],
        ]
    )


def test_polygon_holes_multipolygon_and_invalid_topology(analysis_service):
    polygon = polygon_with_hole()
    result = analysis_service.search("places", "place", polygon=polygon)
    assert {f["id"] for f in result.data["features"]} == {uid(1), uid(4)}
    assert analysis_service.summarize("places", "place", polygon=polygon).data["total"] == 2
    clipped = analysis_service.summarize(
        "buildings",
        "building",
        polygon=polygon,
        aggregations=[Aggregation(field="@area_m2", op="sum", label="area")],
    )
    assert 20000 < clipped.data["metrics"]["area"] < 20200
    multi = Polygon(type="MultiPolygon", coordinates=[polygon.coordinates])
    assert analysis_service.search("places", "place", polygon=multi).data["returned"] == 2
    crossed = Polygon(
        coordinates=[
            [
                [139.75, 35.68],
                [139.752, 35.682],
                [139.75, 35.682],
                [139.752, 35.68],
                [139.75, 35.68],
            ]
        ]
    )
    with pytest.raises(QueryError, match="INVALID_POLYGON"):
        analysis_service.search("places", "place", polygon=crossed)


def test_numeric_geometry_aggregates_and_clipping(analysis_service):
    service = analysis_service
    aggregate = service.summarize(
        "places",
        "place",
        AREA,
        group_by=["country", "taxonomy.primary"],
        aggregations=[
            Aggregation(field="height", op="sum", label="sum"),
            Aggregation(field="height", op="avg", label="avg"),
            Aggregation(field="height", op="min", label="min"),
            Aggregation(field="height", op="max", label="max"),
            Aggregation(field="country", op="count_distinct", label="countries"),
        ],
    )
    assert aggregate.data["metrics"] == {
        "sum": 70,
        "avg": pytest.approx(70 / 3),
        "min": 10,
        "max": 40,
        "countries": 1,
    }
    assert aggregate.data["groups"][0]["value"] == {"country": "JP", "taxonomy.primary": "cafe"}
    narrow = Bounds(west=139.75, east=139.751, south=35.6798, north=35.6802)
    area = [Aggregation(field="@area_m2", op="sum", label="area")]
    full = service.summarize(
        "buildings", "building", narrow, aggregations=area, clip_geometry=False
    )
    clipped = service.summarize("buildings", "building", narrow, aggregations=area)
    assert 30000 < full.data["metrics"]["area"] < 30300
    assert 4000 < clipped.data["metrics"]["area"] < 4050
    length = [Aggregation(field="@length_m", op="sum", label="road_m")]
    roads = service.summarize("transportation", "segment", narrow, aggregations=length)
    assert 89 < roads.data["metrics"]["road_m"] < 92
    measured = service.search("buildings", "building", narrow, include_metrics=True)
    assert measured.data["features"][0]["metrics"]["area_m2"] == pytest.approx(
        full.data["metrics"]["area"]
    )
    distances = service.summarize(
        "places",
        "place",
        center=CENTER,
        radius_m=500,
        aggregations=[Aggregation(field="@distance_m", op="max", label="furthest")],
    )
    assert 360 < distances.data["metrics"]["furthest"] < 365


def test_join_orientation_counts_zero_matches_and_pagination(analysis_service):
    service = analysis_service
    areas = Dataset(theme="divisions", feature_type="division_area", fields=["names.primary"])
    places = Dataset(theme="places", feature_type="place", fields=["names.primary"])
    first = service.spatial_join(places, areas, AREA, relation="within", limit=1)
    assert first.data["total_pairs"] == 2 and first.next_cursor
    second = service.spatial_join(
        places, areas, AREA, relation="within", limit=1, cursor=first.next_cursor
    )
    assert {(p["left_id"], p["right_id"]) for p in first.data["pairs"] + second.data["pairs"]} == {
        (uid(1), uid(101)),
        (uid(2), uid(101)),
    }
    groups = service.spatial_join(areas, places, AREA, relation="contains", mode="group_left")
    assert groups.data["groups"] == [
        {"left_id": uid(101), "count": 2},
        {"left_id": uid(102), "count": 0},
    ]
    assert (
        service.spatial_join(areas, places, AREA, relation="contains", mode="count").data[
            "total_pairs"
        ]
        == 2
    )
    roads = Dataset(theme="transportation", feature_type="segment")
    near = service.spatial_join(places, roads, AREA, relation="within_distance", distance_m=1)
    assert near.data["total_pairs"] == 3
    assert all(p["distance_m"] < 0.01 for p in near.data["pairs"])
    with pytest.raises(QueryError, match="INVALID_CURSOR"):
        service.spatial_join(areas, places, AREA, relation="contains", cursor=first.next_cursor)


def test_snapshot_diff_and_registry_resolution(analysis_service):
    service = analysis_service
    diff = service.compare_releases("places", "place", AREA, BEFORE, AFTER, limit=1)
    assert diff.data["counts"] == {"added": 1, "removed": 1, "modified": 1, "unchanged": 1}
    assert diff.data["schema_added"] == ["extra_note"]
    changes = list(diff.data["changes"])
    while diff.next_cursor:
        diff = service.compare_releases(
            "places", "place", AREA, BEFORE, AFTER, limit=1, cursor=diff.next_cursor
        )
        changes.extend(diff.data["changes"])
    assert [c["id"] for c in changes] == [uid(1), uid(3), uid(4)]
    assert service.compare_releases("places", "place", AREA, AFTER, AFTER).data["changes"] == []
    live = service.resolve_id(uid(1))
    assert (
        live.data["status"] == "live" and live.data["feature"]["names"]["primary"] == "Cafe Updated"
    )
    assert live.data["feature"]["geometry"]["coordinates"] == [139.75, 35.68]
    assert service.resolve_id(uid(3)).data["status"] == "removed"
    assert service.resolve_id(uid(999)).data["status"] == "not_in_registry"
    assert service.get_feature("places", "place", None, AFTER, uid(1)).data["returned"] == 1
    with pytest.raises(QueryError, match="ID_SCOPE_MISMATCH"):
        service.get_feature("places", "place", None, BEFORE, uid(1))


def test_limits_and_untrusted_fields(analysis_service):
    service = analysis_service
    for field in ["names.primary);DROP TABLE x;--", "missing", "names.no_such_field"]:
        with pytest.raises(QueryError, match="INVALID_FIELD"):
            service.search("places", "place", AREA, fields=[field])
    for field in ("names", "height"):
        with pytest.raises(QueryError, match="INVALID_FILTER"):
            service.search(
                "places",
                "place",
                AREA,
                filters=[AttributeFilter(field=field, op="starts_with", value="1")],
            )
    with pytest.raises(QueryError, match="INVALID_AREA"):
        service.search("places", "place", AREA, center=CENTER, radius_m=100)
    with pytest.raises(QueryError, match="INVALID_AGGREGATION"):
        service.summarize(
            "places",
            "place",
            AREA,
            aggregations=[Aggregation(field="names.primary", op="avg", label="bad")],
        )
    with pytest.raises(QueryError, match="INVALID_AREA"):
        circle_bounds(Point(longitude=180, latitude=0), 100)
    with pytest.raises(ValidationError):
        Polygon(coordinates=[[[0, 0], [1, 1], [0, 1], [0, 0]]])  # exceeds area cap


@pytest.mark.parametrize("theme,kind", [(t, k) for t, kinds in TYPES.items() for k in kinds])
def test_fifteen_types_with_point_line_area_fixtures(analysis_service, theme, kind):
    result = analysis_service.search(theme, kind, AREA, include_geometry=True, include_metrics=True)
    assert result.data["returned"] > 0
    assert analysis_service.summarize(theme, kind, AREA).data["total"] == result.data["returned"]
    assert all("geometry" in f and "sources" in f for f in result.data["features"])
    if kind in {"segment", "division_boundary"}:
        assert result.data["features"][0]["geometry"]["type"] == "LineString"
        assert result.data["features"][0]["metrics"]["length_m"] > 700
    if kind in {"building", "division_area", "land_use"}:
        assert result.data["features"][0]["geometry"]["type"] == "Polygon"
        assert result.data["features"][0]["metrics"]["area_m2"] > 30000


def test_empty_manifest_comparison_and_zero_join(analysis_service, monkeypatch):
    catalog = analysis_service.catalog
    original = catalog.files

    def empty_before(release, theme, kind, bounds=None):
        if release == BEFORE and bounds is not None:
            return []
        return original(release, theme, kind, bounds)

    monkeypatch.setattr(catalog, "files", empty_before)
    diff = analysis_service.compare_releases("places", "place", AREA, BEFORE, AFTER)
    assert diff.data["counts"] == {"added": 3, "removed": 0, "modified": 0, "unchanged": 0}
    assert diff.data["before_count"] == 0
    zero = Dataset(
        theme="places",
        feature_type="place",
        filters=[AttributeFilter(field="height", op="lt", value=0)],
    )
    areas = Dataset(theme="divisions", feature_type="division_area")
    join = analysis_service.spatial_join(areas, zero, AREA, mode="group_left", limit=1)
    assert join.data["total_pairs"] == 0 and join.next_cursor
    assert join.data["groups"] == [{"left_id": uid(101), "count": 0}]
    more = analysis_service.spatial_join(
        areas, zero, AREA, mode="group_left", limit=1, cursor=join.next_cursor
    )
    assert more.data["groups"] == [{"left_id": uid(102), "count": 0}]


def test_candidate_cap_and_registry_path_preflight(analysis_service, monkeypatch):
    original = analysis_service.query
    calls = []

    def too_many(sql, params):
        calls.append(sql)
        if "count(*) AS total" in sql:
            return [{"total": 5001}]
        return original(sql, params)

    monkeypatch.setattr(analysis_service, "query", too_many)
    with pytest.raises(QueryError, match="TOO_MANY_CANDIDATES"):
        analysis_service.spatial_join(
            Dataset(theme="places", feature_type="place"),
            Dataset(theme="buildings", feature_type="building"),
            AREA,
        )
    assert not any(" JOIN " in sql for sql in calls)

    def bad_registry(sql, params):
        rows = original(sql, params)
        if params[0] == REGISTRY:
            rows[0]["path"] = "../../private.parquet"
        return rows

    monkeypatch.setattr(analysis_service, "query", bad_registry)
    with pytest.raises(QueryError, match="REGISTRY_CHANGED"):
        analysis_service.resolve_id(uid(1))


def test_distance_ties_and_cursor_projection_scope(fixture_service, bounds):
    first = fixture_service.search(
        "places", "place", bounds, center=CENTER, sort_by="distance", limit=1, fields=["id"]
    )
    second = fixture_service.search(
        "places",
        "place",
        bounds,
        center=CENTER,
        sort_by="distance",
        limit=1,
        fields=["id"],
        cursor=first.next_cursor,
    )
    assert first.data["features"][0]["id"] != second.data["features"][0]["id"]
    assert first.data["features"][0]["distance_m"] == second.data["features"][0]["distance_m"]
    with pytest.raises(QueryError, match="INVALID_CURSOR"):
        fixture_service.search(
            "places",
            "place",
            bounds,
            center=CENTER,
            sort_by="distance",
            fields=["names"],
            cursor=first.next_cursor,
        )


@pytest.mark.asyncio
async def test_new_tools_mcp_contract(analysis_service, monkeypatch):
    monkeypatch.setattr(server.client, "_service", analysis_service)
    async with Client(server.mcp) as client:
        schemas = {t.name: t.output_schema for t in (await client.list_tools()).tools}
        cases = {
            "overture_nearest": {
                "theme": "places",
                "feature_type": "place",
                "center": CENTER.model_dump(),
                "radius_m": 100,
            },
            "overture_spatial_join": {
                "left": {"theme": "places", "feature_type": "place"},
                "right": {"theme": "divisions", "feature_type": "division_area"},
                "bounds": AREA.model_dump(),
                "relation": "within",
            },
            "overture_compare_releases": {
                "theme": "places",
                "feature_type": "place",
                "bounds": AREA.model_dump(),
                "before": BEFORE,
                "after": AFTER,
            },
            "overture_resolve_id": {"identifier": uid(1)},
            "overture_get_feature": {
                "theme": "places",
                "feature_type": "place",
                "identifier": uid(1),
            },
            "overture_summarize": {
                "theme": "places",
                "feature_type": "place",
                "center": CENTER.model_dump(),
                "radius_m": 100,
                "filters": [{"field": "confidence", "op": "gte", "value": 0.8}],
                "aggregations": [{"field": "height", "op": "sum", "label": "height_total"}],
            },
        }
        for tool, params in cases.items():
            result = await client.call_tool(tool, params)
            assert not result.is_error, result.content
            assert result.structured_content is not None
            validate(result.structured_content, schemas[tool])
            assert isinstance(result.content[0], TextContent)
            assert json.loads(result.content[0].text) == result.structured_content
