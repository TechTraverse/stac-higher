"""The input planner (GOES spec §3): pure, no I/O."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from pipeline.process.inputs import (
    InputPlanError,
    input_env,
    parse_canonical_href,
    plan_inputs,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures"
FIXTURE = json.loads((FIXTURES / "process-input-manifest.json").read_text())
EXTRACT_FIXTURE = json.loads((FIXTURES / "process-extract-manifest.json").read_text())


def _documents(given):
    return {tuple(k.split("/", 1)): v for k, v in given["documents"].items()}


def _source_hrefs(given):
    """`{collection}/{item_id}` → `{filename: source href}`, as the ledger
    reports it for reference-mode assets."""
    return {tuple(k.split("/", 1)): v for k, v in given.get("source_hrefs", {}).items()}


def _at(fixture, path: str):
    """Walk a fixture `$ref` path (`given.documents.<key>`, `given.refs.0.draft`):
    dotted segments, integers indexing lists. Document keys hold a `/` but never
    a `.`, so a plain split is unambiguous."""
    node = fixture
    for seg in path.split("."):
        node = node[int(seg)] if isinstance(node, list) else node[seg]
    return node


def _resolve_refs(expected_manifest, fixture):
    out = copy.deepcopy(expected_manifest)
    for entry in out["items"]:
        entry["item"] = _at(fixture, entry["item"]["$ref"])
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
    assert [asdict(f) for f in plan.fetches] == expected["fetches"]
    assert plan.manifest == _resolve_refs(expected["manifest"], FIXTURE)


def test_extract_plan_matches_the_golden_fixture():
    given, expected = EXTRACT_FIXTURE["given"], EXTRACT_FIXTURE["expected"]
    plan = plan_inputs(
        run_id=given["run_id"],
        process_id=given["process_id"],
        batch_id=given["batch_id"],
        kind="extract",
        refs=given["refs"],
        documents=_documents(given),
        source_collections=given["source_collections"],
        bucket=given["bucket"],
        asset_href_base=given["asset_href_base"],
        source_hrefs=_source_hrefs(given),
    )
    assert plan.manifest_key == expected["manifest_key"]
    assert list(plan.read_prefixes) == expected["read_prefixes"]
    assert [asdict(f) for f in plan.fetches] == expected["fetches"]
    assert plan.manifest == _resolve_refs(expected["manifest"], EXTRACT_FIXTURE)


def test_a_draft_ref_needs_no_pgstac_document():
    """An extractor's item is not catalogued yet — the ref carries it, so a
    missing pgstac document must not skip the item."""
    plan = plan_inputs(
        run_id="r",
        process_id="p",
        batch_id="b",
        kind="extract",
        refs=[{"item_id": "i", "collection_id": "c", "draft": {"id": "i", "assets": {}}}],
        documents={},
        source_collections=["c"],
        bucket="b",
        asset_href_base="/api/assets",
    )
    assert plan.manifest["items"][0]["item"] == {"id": "i", "assets": {}}
    assert "skipped" not in plan.manifest


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


def test_reference_mode_canonical_href_is_staged_from_its_source():
    doc = {
        "id": "i", "collection": "c",
        "assets": {"scene": {"href": "/api/assets/c/i/scene.nc"}},
    }
    plan = plan_inputs(
        run_id="r", process_id="p", batch_id="b", kind="transform",
        refs=[{"item_id": "i", "collection_id": "c"}],
        documents={("c", "i"): doc}, source_collections=["c"],
        bucket="stac-higher", asset_href_base="/api/assets",
        source_hrefs={("c", "i"): {"scene.nc": "https://src.example/x/scene.nc"}},
    )
    (fetch,) = plan.fetches
    assert fetch.href == "https://src.example/x/scene.nc"
    assert fetch.key == "staging/runs/r/inputs/b/i/scene.nc"
    asset = plan.manifest["items"][0]["assets"]["scene"]
    assert asset["staged"] is True and asset["key"] == fetch.key
    # The manifest keeps the CATALOG href for provenance, not the source URL.
    assert asset["href"] == "/api/assets/c/i/scene.nc"
    # Still granted: the collection prefix (a copy-mode sibling may need it).
    assert list(plan.read_prefixes) == ["assets/c/"]


def test_canonical_href_without_a_source_href_stays_platform_held():
    doc = {"id": "i", "collection": "c", "assets": {"a": {"href": "/api/assets/c/i/a.tif"}}}
    plan = plan_inputs(
        run_id="r", process_id="p", batch_id="b", kind="transform",
        refs=[{"item_id": "i", "collection_id": "c"}], documents={("c", "i"): doc},
        source_collections=["c"], bucket="stac-higher", asset_href_base="/api/assets",
        source_hrefs={("c", "i"): {"other.tif": "https://src.example/other.tif"}},
    )
    assert plan.fetches == ()
    assert plan.manifest["items"][0]["assets"]["a"]["key"] == "assets/c/i/a.tif"
