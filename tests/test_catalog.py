import httpx
import pytest

from overture_maps_mcp.catalog import Catalog
from overture_maps_mcp.models import Bounds, QueryError


def test_catalog_discovery_cache_and_unavailable(monkeypatch):
    calls = []

    def get(client, url):
        calls.append(url)
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "latest": "2026-09-23.1",
                "links": [
                    {
                        "rel": "child",
                        "href": "https://stac.overturemaps.org/2026-09-23.1/catalog.json",
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.Client, "get", get)
    catalog = Catalog()
    assert catalog.resolve()[0] == "2026-09-23.1"
    assert catalog.resolve()[1] == ["2026-09-23.1"]
    assert len(calls) == 1
    with pytest.raises(QueryError, match="RELEASE_UNAVAILABLE"):
        catalog.resolve("2020-01-01.0")
    with pytest.raises(QueryError, match="INVALID_RELEASE"):
        catalog.resolve("../secrets")


def test_catalog_failure_is_not_empty_success(monkeypatch):
    def get(client, url):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "get", get)
    with pytest.raises(QueryError, match="CATALOG_UNAVAILABLE"):
        Catalog().resolve()


@pytest.mark.parametrize("bad_asset", [False, True])
def test_manifest_selection_cache_and_failure(monkeypatch, bad_asset):
    release = "2026-09-23.1"
    prefix = f"https://stac.overturemaps.org/{release}/places/place/"
    asset = (
        "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
        f"release/{release}/theme=places/type=place/part-00000.parquet"
    )
    calls = []

    def get(client, url):
        calls.append(url)
        if url.endswith("collection.json"):
            data = {
                "partition:file_count": 2,
                "links": [
                    {"rel": "item", "href": prefix + f"{n:05d}/{n:05d}.json"} for n in range(2)
                ],
            }
        else:
            data = {
                "bbox": [139, 35, 140, 36] if "00000" in url else [-120, 30, -110, 40],
                "assets": {"aws": {"href": "https://evil.example/file" if bad_asset else asset}},
            }
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    catalog = Catalog()
    catalog._releases, catalog._latest, catalog._expires = [release], release, float("inf")
    bounds = Bounds(west=139.75, south=35.68, east=139.76, north=35.69)
    if bad_asset:
        with pytest.raises(QueryError, match="MANIFEST_UNAVAILABLE"):
            catalog.files(release, "places", "place", bounds)
        assert not catalog._items
        return
    assert catalog.files(release, "places", "place", bounds) == [asset]
    count = len(calls)
    assert catalog.files(release, "places", "place", bounds) == [asset]
    assert len(calls) == count
    empty = Bounds(west=0, south=0, east=0.01, north=0.01)
    assert catalog.files(release, "places", "place", empty) == []


def test_missing_shard_is_failure(monkeypatch):
    def get(client, url):
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={"partition:file_count": 2, "links": []},
        )

    monkeypatch.setattr(httpx.Client, "get", get)
    catalog = Catalog()
    catalog._releases = ["2026-09-23.1"]
    catalog._expires = float("inf")
    with pytest.raises(QueryError, match="MANIFEST_UNAVAILABLE"):
        catalog.files("2026-09-23.1", "places", "place")


@pytest.mark.parametrize(
    "xmax,valid",
    [(180.00001525878906, True), (180.00022888183594, True), (180.1, False), (float("nan"), False)],
)
def test_world_edge_float32_extent(monkeypatch, xmax, valid):
    release = "2026-09-23.1"
    prefix = f"https://stac.overturemaps.org/{release}/base/bathymetry/"
    asset = (
        "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
        f"release/{release}/theme=base/type=bathymetry/part-00000.parquet"
    )

    def get(client, url):
        if url.endswith("collection.json"):
            data = {
                "partition:file_count": 1,
                "links": [{"rel": "item", "href": prefix + "00000/00000.json"}],
            }
        else:
            data = {"bbox": [-180.0, -90.0, xmax, 90.0], "assets": {"aws": {"href": asset}}}
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    catalog = Catalog()
    catalog._releases, catalog._latest, catalog._expires = [release], release, float("inf")
    bounds = Bounds(west=179.99, east=180, south=0, north=0.01)
    if valid:
        assert catalog.files(release, "base", "bathymetry", bounds) == [asset]
        assert next(iter(catalog._items.values()))[0][0] == [-180.0, -90.0, 180.0, 90.0]
    else:
        with pytest.raises(QueryError, match="MANIFEST_UNAVAILABLE"):
            catalog.files(release, "base", "bathymetry", bounds)
