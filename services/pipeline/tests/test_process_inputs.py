"""The input planner (GOES spec §3): pure, no I/O."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pipeline.process.inputs import (
    InputPlanError,
    input_env,
    parse_canonical_href,
    plan_inputs,
)

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "tests"
        / "contract-fixtures"
        / "process-input-manifest.json"
    ).read_text()
)


def _documents(given):
    return {tuple(k.split("/", 1)): v for k, v in given["documents"].items()}


def _resolve_refs(expected_manifest, given):
    out = copy.deepcopy(expected_manifest)
    for entry in out["items"]:
        ref = entry["item"]["$ref"].removeprefix("given.documents.")
        entry["item"] = given["documents"][ref]
    return out


def test_plan_matches_the_golden_fixture():
    given, expected = FIXTURE["given"], FIXTURE["expected"]
    plan = plan_inputs(
        run_id=given["run_id"],
        process_id=given["process_id"],
        batch_id=given["batch_id"],
        kind="transform",
        refs=given["refs"],
        documents=_documents(given),
        source_collections=given["source_collections"],
        bucket=given["bucket"],
        asset_href_base=given["asset_href_base"],
    )
    assert plan.manifest_key == expected["manifest_key"]
    assert list(plan.read_prefixes) == expected["read_prefixes"]
    assert [f.__dict__ for f in plan.fetches] == expected["fetches"]
    assert plan.manifest == _resolve_refs(expected["manifest"], given)


@pytest.mark.parametrize(
    "href,parsed",
    [
        ("/api/assets/c/i/f.tif", ("c", "i", "f.tif")),
        ("/api/assets/c/i/f%20g.tif", ("c", "i", "f g.tif")),
        ("https://x/y.tif", None),
        ("/api/assets/c/i", None),
        ("/api/assets/c/../f", None),
        (None, None),
    ],
)
def test_parse_canonical_href(href, parsed):
    assert parse_canonical_href(href) == parsed


def test_legacy_ref_without_collection_uses_the_single_source_collection():
    doc = {"id": "i", "collection": "only", "assets": {}}
    plan = plan_inputs(
        run_id=FIXTURE["given"]["run_id"],
        process_id="p",
        batch_id="b",
        kind="transform",
        refs=[{"item_id": "i"}],
        documents={("only", "i"): doc},
        source_collections=["only"],
        bucket="b",
        asset_href_base="/api/assets",
    )
    assert plan.manifest["items"][0]["collection"] == "only"
    assert plan.manifest["items"][0]["op"] == "unknown"


def test_legacy_ref_is_ambiguous_with_two_source_collections():
    with pytest.raises(InputPlanError, match="collection"):
        plan_inputs(
            run_id=FIXTURE["given"]["run_id"],
            process_id="p",
            batch_id="b",
            kind="transform",
            refs=[{"item_id": "i"}],
            documents={},
            source_collections=["a", "b"],
            bucket="b",
            asset_href_base="/api/assets",
        )


def test_asset_without_a_usable_href_is_omitted_from_assets_but_item_kept():
    doc = {
        "id": "i",
        "collection": "c",
        "assets": {"odd": {"title": "no href"}, "rel": {"href": "a/b.tif"}},
    }
    plan = plan_inputs(
        run_id=FIXTURE["given"]["run_id"],
        process_id="p",
        batch_id="b",
        kind="transform",
        refs=[{"item_id": "i", "collection_id": "c", "op": "insert"}],
        documents={("c", "i"): doc},
        source_collections=["c"],
        bucket="b",
        asset_href_base="/api/assets",
    )
    assert plan.manifest["items"][0]["assets"] == {}
    assert plan.fetches == ()


def test_input_env_names_prefix_and_manifest():
    env = input_env(FIXTURE["given"]["run_id"], "staging/runs/x/inputs/b/manifest.json")
    assert env == {
        "STAC_HIGHER_INPUT_PREFIX": f"staging/runs/{FIXTURE['given']['run_id']}/inputs/",
        "STAC_HIGHER_INPUT_MANIFEST": "staging/runs/x/inputs/b/manifest.json",
    }
