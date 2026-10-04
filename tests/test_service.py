from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from overture_maps_mcp.models import TYPES, Bounds, QueryError
from overture_maps_mcp.query import bounded
from overture_maps_mcp.service import Service
from tests.conftest import IDS, RELEASE


@pytest.mark.parametrize("theme,kind", [(t, k) for t, kinds in TYPES.items() for k in kinds])
def test_all_types_sql_and_exact_geometry(fixture_service: Service, bounds: Bounds, theme, kind):
    result = fixture_service.search(theme, kind, bounds)
    assert result.release == RELEASE
    assert result.data["returned"] == 3
    assert [row["id"] for row in result.data["features"]] == IDS[:3]
    assert result.license and result.attribution_url
    assert fixture_service.summarize(theme, kind, bounds).data["total"] == 3
    assert "geometry" in fixture_service.schema(theme, kind, RELEASE).data["columns"]


def test_cursor_detail_and_empty_scope(fixture_service: Service, bounds: Bounds):
    first = fixture_service.search("places", "place", bounds, RELEASE, limit=2)
    second = fixture_service.search(
        "places", "place", bounds, RELEASE, limit=2, cursor=first.next_cursor
    )
    assert second.next_cursor is None
    assert [row["id"] for row in second.data["features"]] == [IDS[2]]
    detail = fixture_service.search(
        "places", "place", bounds, RELEASE, identifier=IDS[2], include_geometry=True, limit=1
    )
    assert detail.data["features"][0]["geometry"] == {
        "type": "Point",
        "coordinates": [139.75, 35.68],
    }
    with pytest.raises(QueryError, match="INVALID_CURSOR"):
        fixture_service.search(
            "places", "place", bounds, RELEASE, name="changed", cursor=first.next_cursor
        )
    empty = Bounds(west=139.9, south=35.67, east=139.91, north=35.70)
    assert fixture_service.search("places", "place", empty).data["returned"] == 0
    assert fixture_service.summarize("places", "place", empty).data["total"] == 0


def test_filters_and_sql_injection(fixture_service: Service, bounds: Bounds):
    high = fixture_service.search("places", "place", bounds, category="cafe", min_confidence=0.8)
    assert high.data["returned"] == 2
    injection = fixture_service.search("places", "place", bounds, name="%' OR 1=1 --")
    assert injection.data["returned"] == 0
    assert fixture_service.search("places", "place", bounds, name="CAFE").data["returned"] == 3
    with pytest.raises(QueryError, match="UNSUPPORTED_FILTER"):
        fixture_service.search("buildings", "building", bounds, category="cafe")
    with pytest.raises(QueryError, match="UNSUPPORTED_FILTER"):
        fixture_service.search("addresses", "address", bounds, name="foo")
    with pytest.raises(QueryError, match="INVALID_TYPE"):
        fixture_service.search("places", "../../private", bounds)
    with pytest.raises(QueryError, match="INVALID_ID"):
        fixture_service.search("places", "place", bounds, identifier="bad")


def test_full_counts_and_null_groups(fixture_service: Service, bounds: Bounds):
    summary = fixture_service.summarize("places", "place", bounds, "basic_category")
    assert summary.data["total"] == 3
    assert summary.data["groups"] == [
        {"value": "food_and_drink", "count": 2},
        {"value": None, "count": 1},
    ]
    assert summary.data["other_count"] == 0
    assert summary.data["groups_truncated"] is False
    with pytest.raises(QueryError, match="INVALID_GROUP"):
        fixture_service.summarize("places", "place", bounds, 'class"; DROP TABLE fixture;--')


@pytest.mark.parametrize(
    "update",
    [
        {"west": 180, "east": -180},
        {"east": 150},
        {"north": float("nan")},
        {"south": 91},
    ],
)
def test_invalid_bounds(bounds: Bounds, update):
    with pytest.raises(ValidationError):
        Bounds.model_validate(bounds.model_dump() | update)


def test_response_bound():
    with pytest.raises(QueryError, match="RESULT_TOO_LARGE"):
        bounded({"geometry": "x" * 400_001})
    assert json.dumps(bounded({"total": 0}))
