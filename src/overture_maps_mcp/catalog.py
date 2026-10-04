from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from time import monotonic
from typing import Any

import httpx

from overture_maps_mcp.models import TYPES, Bounds, QueryError, Theme

CATALOG_URL = "https://stac.overturemaps.org/catalog.json"
RELEASE_PATTERN = re.compile(r"20\d{2}-\d{2}-\d{2}\.\d+")


class Catalog:
    def __init__(self) -> None:
        self._expires = 0.0
        self._releases: list[str] = []
        self._latest = ""
        self._collections: dict[str, list[str]] = {}
        self._items: dict[str, list[tuple[list[float], str]]] = {}

    def resolve(self, release: str | None = None) -> tuple[str, list[str]]:
        if release is not None and not RELEASE_PATTERN.fullmatch(release):
            raise QueryError("INVALID_RELEASE: use a release such as 2026-09-23.1 or omit it.")
        if monotonic() >= self._expires:
            try:
                with httpx.Client(timeout=15, follow_redirects=False, trust_env=False) as client:
                    response = client.get(CATALOG_URL)
                    response.raise_for_status()
                    payload = response.json()
                releases = []
                for link in payload["links"]:
                    if link.get("rel") == "child":
                        match = re.search(
                            r"/(20\d{2}-\d{2}-\d{2}\.\d+)/catalog\.json$", link["href"]
                        )
                        if match:
                            releases.append(match[1])
                latest = payload["latest"]
                if not isinstance(latest, str) or latest not in releases:
                    raise ValueError("invalid catalog")
                self._releases, self._latest = sorted(set(releases)), latest
                self._expires = monotonic() + 600
                self._collections.clear()
                self._items.clear()
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise QueryError(
                    "CATALOG_UNAVAILABLE: cannot verify available releases; retry later."
                ) from exc
        selected = release or self._latest
        if selected not in self._releases:
            raise QueryError(
                "RELEASE_UNAVAILABLE: call overture_catalog and use an available release."
            )
        return selected, list(self._releases)

    def files(
        self, release: str, theme: Theme, feature_type: str, bounds: Bounds | None = None
    ) -> list[str]:
        """Select official GeoParquet shards by STAC extent before querying rows."""
        if theme not in TYPES or feature_type not in TYPES[theme]:
            raise QueryError("INVALID_TYPE: call overture_catalog for valid theme/type pairs.")
        self.resolve(release)
        prefix = f"https://stac.overturemaps.org/{release}/{theme}/{feature_type}/"
        asset_prefix = (
            "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
            f"release/{release}/theme={theme}/type={feature_type}/"
        )
        try:
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                if prefix not in self._collections:
                    response = client.get(prefix + "collection.json")
                    response.raise_for_status()
                    payload = response.json()
                    links = [link["href"] for link in payload["links"] if link.get("rel") == "item"]
                    if (
                        not links
                        or len(links) > 4096
                        or len(set(links)) != len(links)
                        or len(links) != payload["partition:file_count"]
                        or any(
                            not re.fullmatch(re.escape(prefix) + r"\d+/\d+\.json", url)
                            for url in links
                        )
                    ):
                        raise ValueError("invalid shard manifest")
                    self._collections[prefix] = links

                def item(url: str) -> tuple[list[float], str]:
                    response = client.get(url)
                    response.raise_for_status()
                    payload: dict[str, Any] = response.json()
                    extent = payload["bbox"]
                    href = payload["assets"]["aws"]["href"]
                    if (
                        not isinstance(extent, list)
                        or len(extent) != 4
                        or any(not isinstance(v, int | float) for v in extent)
                        # Official float32/raster extents slightly overshoot world
                        # edges (bathymetry and land_cover). Clamp only metadata;
                        # user coordinates and feature geometries stay unchanged.
                        or not -180.001 <= extent[0] <= extent[2] <= 180.001
                        or not -90.001 <= extent[1] <= extent[3] <= 90.001
                        or not re.fullmatch(
                            re.escape(asset_prefix) + r"part-[\w.-]+\.parquet", href
                        )
                    ):
                        raise ValueError("invalid shard extent or asset")
                    extent = [
                        max(-180.0, extent[0]),
                        max(-90.0, extent[1]),
                        min(180.0, extent[2]),
                        min(90.0, extent[3]),
                    ]
                    return extent, href

                if bounds is None:
                    # One file suffices to inspect the schema; avoid a worldwide manifest fetch.
                    if prefix in self._items:
                        return [self._items[prefix][0][1]]
                    return [item(self._collections[prefix][0])[1]]
                if prefix not in self._items:
                    deadline = monotonic() + 30

                    def timed_item(url: str) -> tuple[list[float], str]:
                        if monotonic() >= deadline:
                            raise ValueError("shard discovery time limit exceeded")
                        return item(url)

                    with ThreadPoolExecutor(max_workers=12) as pool:
                        self._items[prefix] = list(pool.map(timed_item, self._collections[prefix]))
                return [
                    href
                    for extent, href in self._items[prefix]
                    if extent[0] <= bounds.east
                    and extent[2] >= bounds.west
                    and extent[1] <= bounds.north
                    and extent[3] >= bounds.south
                ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise QueryError(
                "MANIFEST_UNAVAILABLE: cannot verify the complete shard manifest; retry later."
            ) from exc


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()
