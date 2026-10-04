"""Opt-in public-data checks for the core wheel's Python API and actual CLI executable."""

from __future__ import annotations

import importlib.util
import json
import subprocess

from overture_maps_mcp import Bounds, Client


def main() -> None:
    assert importlib.util.find_spec("mcp") is None
    client = Client()
    area = Bounds(west=139.760, south=35.680, east=139.762, north=35.682)
    release = client.catalog().release
    params = {
        "theme": "places",
        "feature_type": "place",
        "bounds": area.model_dump(),
        "release": release,
        "fields": ["names.primary"],
        "limit": 2,
    }
    expected = client.search(
        "places", "place", bounds=area, release=release, fields=["names.primary"], limit=2
    ).model_dump(mode="json")
    assert expected["data"]["returned"] == 2
    result = subprocess.run(
        ["overture-maps", "search", "--params", "-"],
        input=json.dumps(params),
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert not result.stderr and json.loads(result.stdout) == expected
    summary = client.summarize("places", "place", bounds=area, release=release)
    result = subprocess.run(
        [
            "overture-maps",
            "summarize",
            "--theme",
            "places",
            "--type",
            "place",
            "--bounds",
            "139.760",
            "35.680",
            "139.762",
            "35.682",
            "--release",
            release,
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(result.stdout) == summary.model_dump(mode="json")
    assert summary.data["total"] >= expected["data"]["returned"]
    print(
        json.dumps(
            {
                "release": release,
                "search_returned": 2,
                "summary_total": summary.data["total"],
                "mcp_installed": False,
                "cli_python_identical": True,
            }
        )
    )


if __name__ == "__main__":
    main()
