from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from overture_maps_mcp import Client, Response, api, cli
from overture_maps_mcp.storage import Storage, StorageError
from tests.test_analysis import AFTER, AREA, BEFORE, CENTER, analysis_service, uid

# Import the geographic fixture, not an alternate API implementation.
__all__ = ["analysis_service"]
CASES = [
    ("catalog", {}),
    ("schema", {"theme": "places", "feature_type": "place"}),
    ("search", {"theme": "places", "feature_type": "place", "bounds": AREA.model_dump()}),
    ("get_feature", {"theme": "places", "feature_type": "place", "identifier": uid(1)}),
    ("summarize", {"theme": "places", "feature_type": "place", "bounds": AREA.model_dump()}),
    (
        "nearest",
        {
            "theme": "places",
            "feature_type": "place",
            "center": CENTER.model_dump(),
            "radius_m": 100,
        },
    ),
    (
        "spatial_join",
        {
            "left": {"theme": "places", "feature_type": "place"},
            "right": {"theme": "divisions", "feature_type": "division_area"},
            "bounds": AREA.model_dump(),
            "relation": "within",
        },
    ),
    (
        "compare_releases",
        {
            "theme": "places",
            "feature_type": "place",
            "bounds": AREA.model_dump(),
            "before": BEFORE,
            "after": AFTER,
        },
    ),
    ("resolve_id", {"identifier": uid(1)}),
]


@pytest.fixture
def public_client(analysis_service, monkeypatch, tmp_path):
    class FixtureClient(Client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._service = analysis_service

    monkeypatch.setattr(api, "Client", FixtureClient)
    return FixtureClient(storage_dir=tmp_path / ".runtime")


@pytest.mark.parametrize("operation,params", CASES)
def test_public_api_and_cli_result_match(public_client, operation, params, capsys):
    expected = getattr(public_client, operation)(**params)
    assert isinstance(expected, Response)
    assert expected.source and expected.license
    assert cli.main([operation.replace("_", "-"), "--params", json.dumps(params)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == expected.model_dump(mode="json")


def test_cli_flags_are_equivalent_to_json(public_client, capsys):
    params = CASES[2][1] | {"limit": 2, "fields": ["names.primary"], "include_geometry": True}
    expected = public_client.search(**params).model_dump(mode="json")
    assert (
        cli.main(
            [
                "--pretty",
                "search",
                "--theme",
                "places",
                "--type",
                "place",
                "--bounds",
                str(AREA.west),
                str(AREA.south),
                str(AREA.east),
                str(AREA.north),
                "--limit",
                "2",
                "--fields",
                "names.primary",
                "--include-geometry",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize(
    "params",
    [
        {"theme": "places", "feature_type": "place", "country": "JP"},
        {"theme": "places", "feature_type": "place", "limit": 51},
        {"theme": "places", "feature_type": "place", "radius_m": float("inf")},
        {"theme": "places", "feature_type": "place", "min_confidence": -1},
        {"theme": "places", "feature_type": "place", "output_format": "bad"},
    ],
)
def test_invalid_input_fails_before_query(public_client, params, monkeypatch, capsys):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input reached the backend")

    monkeypatch.setattr(public_client._service, "search", unexpected)
    with pytest.raises(ValidationError):
        public_client.search(**params)
    assert cli.main(["search", "--params", json.dumps(params)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "INVALID_INPUT"


def test_cli_json_file_stdin_and_errors(public_client, tmp_path, monkeypatch, capsys):
    file = tmp_path / "params.json"
    file.write_text(json.dumps(CASES[2][1]), encoding="utf-8-sig")
    assert cli.main(["search", "--params", "@" + str(file)]) == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(file.read_bytes())))
    assert cli.main(["search", "--params", "-"]) == 0
    capsys.readouterr()
    for value in ("[]", "{", "x" * (cli.MAX_INPUT_BYTES + 1)):
        assert cli.main(["search", "--params", value]) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert json.loads(captured.err)["error"]
    assert cli.main(["search", "--params", json.dumps(CASES[2][1]), "--theme", "places"]) == 2
    assert "DUPLICATE_ARGUMENT" in capsys.readouterr().err
    assert cli.main(["search", "--params", json.dumps(CASES[2][1] | {"feature_type": "bad"})]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "INVALID_TYPE" in captured.err


def test_stable_default_and_instance_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERTURE_MAPS_MCP_STORAGE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME", str(tmp_path))
    first = Client()
    monkeypatch.chdir(tmp_path)
    second = Client()
    assert first.storage_dir == second.storage_dir == tmp_path / "overture-maps-mcp" / ".runtime"
    assert not first.storage_dir.exists()
    assert first.storage()["owned"]["bytes"] == 0
    seen = []

    def execute(sql, params, *, storage):
        seen.append(storage.root)
        return []

    monkeypatch.setattr(api, "execute", execute)
    one = Client(storage_dir=tmp_path / "one" / ".runtime")
    two = Client(storage_dir=tmp_path / "two" / ".runtime")
    one._service.query("", [])
    two._service.query("", [])
    assert seen == [one.storage_dir, two.storage_dir]
    assert "OVERTURE_MAPS_MCP_STORAGE_DIR" not in os.environ


def test_cli_cleanup_preserves_application_and_active_calls(tmp_path, capsys):
    app = tmp_path / "application"
    app.mkdir()
    venv = app / ".venv"
    venv.mkdir()
    (venv / "pyvenv.cfg").write_text("application environment")
    storage = Storage(app)
    argv = ["--storage-dir", str(storage.root)]
    assert cli.main([*argv, "clean"]) == 0
    capsys.readouterr()
    with storage.lease():
        storage.extension_directory().joinpath("blob").write_bytes(b"cache")
        assert cli.main([*argv, "clean"]) == 1
        assert "STORAGE_BUSY" in capsys.readouterr().err
    assert cli.main([*argv, "storage"]) == 0
    assert json.loads(capsys.readouterr().out)["owned"]["bytes"] > 0
    assert cli.main([*argv, "clean"]) == 0
    capsys.readouterr()
    assert not storage.root.exists() and (venv / "pyvenv.cfg").exists()
    with pytest.raises(StorageError, match="UNSAFE_PATH"):
        Client(storage_dir=tmp_path / "not-runtime")


def test_cleanup_refuses_own_environment(tmp_path, monkeypatch):
    storage = Storage(tmp_path)
    storage.initialize()
    monkeypatch.setattr("sys.prefix", str(storage.root / "venv"))
    with pytest.raises(StorageError, match="EXTERNAL_PYTHON_REQUIRED"):
        storage.clean(include_legacy=False)
    assert storage.root.exists()


def test_cli_help_and_unknown_flag(capsys):
    for command in cli._OPERATIONS:
        with pytest.raises(SystemExit) as exc:
            cli.main([command.replace("_", "-"), "--help"])
        assert exc.value.code == 0
        assert "usage:" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        cli.main(["search", "--country", "JP"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["error"]["code"] == "INVALID_ARGUMENT"


def test_relative_platform_cache_cannot_use_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERTURE_MAPS_MCP_STORAGE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA" if os.name == "nt" else "XDG_CACHE_HOME", "relative-cache")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    fallback = home / "AppData" / "Local" if os.name == "nt" else home / ".cache"
    assert Client().storage_dir == fallback / "overture-maps-mcp" / ".runtime"
    assert not fallback.exists()
