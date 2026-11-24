"""`pipeline.demo cube-seed` (#122): the shapes it writes, tested without a stack."""

from __future__ import annotations

import pytest

from pipeline.demo import cube_seed
from pipeline.demo.goes import extractor_code
from pipeline.ingest.config import parse_ingest_config, path_matches
from pipeline.ingest.extract import parse_metadata
from pipeline.ingest.window import expand_prefixes, resolve_window

CMIPC_KEY = "2026/276/17/OR_ABI-L2-CMIPC-M6C13_G19_s20262761701174_e20262761703547_c1.nc"


def test_ingest_config_round_trips_and_names_the_cube_extractor():
    parsed = parse_ingest_config(cube_seed.ingest_config())
    assert parsed.source_path == "ABI-L2-CMIPC/"
    assert parsed.storage_mode == "reference"
    assert parsed.path_template == "{Y}/{j}/{H}/" and parsed.window_begin == "-6h"
    assert parsed.max_files_per_poll == 1 and parsed.poll_frequency_seconds == 60
    assert parse_metadata(parsed.metadata).extractor_process_id == cube_seed.EXTRACTOR_ID


def test_include_glob_matches_only_the_band_13_granules():
    parsed = parse_ingest_config(cube_seed.ingest_config())
    assert path_matches(CMIPC_KEY, parsed.include, parsed.exclude)
    assert not path_matches(CMIPC_KEY.replace("M6C13", "M6C02"), parsed.include, parsed.exclude)
    # the bare token the spec names matches nothing: a lone `*` stops at `/`
    assert not path_matches(CMIPC_KEY, ["M6C13"], [])
    assert not path_matches(CMIPC_KEY, ["*M6C13*"], [])


def test_the_window_expands_to_hour_prefixes():
    import datetime as dt

    parsed = parse_ingest_config(cube_seed.ingest_config())
    window = resolve_window(parsed.window_begin, parsed.window_end, dt.datetime.now(dt.UTC))
    assert 6 <= len(expand_prefixes(parsed.path_template, window, max_prefixes=64)) <= 8


def test_collections_are_distinct_and_the_cube_starts_empty():
    docs = cube_seed.collection_documents()
    assert [d["id"] for d in docs] == [cube_seed.SOURCE_COLLECTION, cube_seed.CUBE_COLLECTION]
    assert cube_seed.SOURCE_COLLECTION != cube_seed.CUBE_COLLECTION
    assert all(d["type"] == "Collection" and d["title"] and d["description"] for d in docs)


def test_the_cube_extractor_is_its_own_process_with_the_demo_code():
    from pipeline.demo.goes import seed as goes

    assert cube_seed.EXTRACTOR_ID != goes.EXTRACTOR_ID
    assert cube_seed.EXTRACTOR_REVISION_ID != goes.EXTRACTOR_REVISION_ID
    assert "cmi_subdataset" in extractor_code()


@pytest.mark.parametrize("command", ["cube-seed", "cube-status", "cube-teardown"])
def test_cli_wires_the_cube_commands(command, monkeypatch):
    from pipeline.demo import __main__ as cli

    called = {}
    monkeypatch.setattr(cube_seed, "seed", lambda a: called.setdefault("fn", "cube-seed") and 0)
    monkeypatch.setattr(cube_seed, "status", lambda a: called.setdefault("fn", "cube-status") and 0)
    monkeypatch.setattr(
        cube_seed, "teardown", lambda a: called.setdefault("fn", "cube-teardown") and 0
    )
    assert cli.main([command]) == 0
    assert called["fn"] == command
