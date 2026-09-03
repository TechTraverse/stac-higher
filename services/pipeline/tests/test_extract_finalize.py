"""Finalize's extract branch (GOES spec §6.2 step 3, §15)."""

from __future__ import annotations

import json

from _ingest_fake import FakeAdapter, FakeIngestRepo, FakeWriter, make_association
from _process_fake import FakeProcessRepo
from pipeline.finalize.extract_run import check_extract_output, finalize_extract_run
from pipeline.ingest.repo import STATUS_EXTRACTING, STATUS_FAILED, STATUS_ITEMIZED
from pipeline.storage.keys import run_staging_prefix

PROC = "5c9f1c2e-0000-4000-8000-0000000000e1"


class FakeStore:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.deleted: list[str] = []

    def list_keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.deleted.append(key)

    def head(self, key):  # pragma: no cover
        return None

    def copy(self, s, d):  # pragma: no cover
        return None


def draft(item_id="scene", href="/api/assets/col/scene/scene.nc"):
    return {
        "type": "Feature", "stac_version": "1.0.0", "stac_extensions": [],
        "id": item_id, "collection": "col", "geometry": None, "bbox": None,
        "properties": {"datetime": None}, "links": [],
        "assets": {"scene.nc": {"href": href, "roles": ["data"]}},
    }


def fixed(d):
    out = json.loads(json.dumps(d))
    out["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    out["bbox"] = [0, 0, 0, 0]
    out["properties"] = {"datetime": "2026-09-03T04:01:17Z", "platform": "GOES-19"}
    out["assets"]["scene.nc"]["type"] = "application/x-netcdf"
    return out


# --- the pure check --------------------------------------------------------

def test_check_accepts_metadata_changes_only():
    assert check_extract_output(draft(), fixed(draft())) is None


def test_check_refuses_id_collection_href_and_asset_set_changes():
    d = draft()
    bad = fixed(d)
    bad["id"] = "other"
    assert "id" in check_extract_output(d, bad)
    bad = fixed(d)
    bad["collection"] = "elsewhere"
    assert "collection" in check_extract_output(d, bad)
    bad = fixed(d)
    bad["assets"]["scene.nc"]["href"] = "https://x/y.nc"
    assert "href" in check_extract_output(d, bad)
    bad = fixed(d)
    bad["assets"]["extra"] = {"href": "z"}
    assert "asset" in check_extract_output(d, bad)
    bad = fixed(d)
    del bad["assets"]["scene.nc"]
    assert "asset" in check_extract_output(d, bad)


def test_check_requires_geometry_and_datetime():
    d = draft()
    bad = fixed(d)
    bad["geometry"] = None
    assert "geometry" in check_extract_output(d, bad)
    bad = fixed(d)
    bad["properties"]["datetime"] = None
    assert "datetime" in check_extract_output(d, bad)


# --- the branch ------------------------------------------------------------

async def _setup(store_objects, *, rows=("scene",)):
    ingest = FakeIngestRepo()
    assoc = make_association(
        {"source_path": "/out", "metadata": {"strategy": "extractor",
                                             "extractor": {"process_id": PROC}}}
    )
    ingest.associations.append(assoc)
    refs = []
    for item_id in rows:
        rid = await ingest.insert_ledger_version(
            assoc.id, f"{item_id}.nc", version=1, status=STATUS_EXTRACTING, size=5, fingerprint="f"
        )
        refs.append({"item_id": item_id, "collection_id": "col", "op": "insert",
                     "ledger_ids": [rid], "draft": draft(item_id)})
    process = FakeProcessRepo(kinds={PROC: "extractor"})
    run_id, _ = await process.enqueue_run_detailed(
        process_id=PROC, revision_id="rev-1", source_id=None, association_id=assoc.id,
        input_items=refs, deferred_until=None,
    )
    await ingest.set_extract_run([r["ledger_ids"][0] for r in refs], run_id)
    return ingest, process, assoc, run_id


async def test_extract_branch_completes_the_item_and_bumps_flow_stats():
    ingest, process, assoc, run_id = await _setup({})
    prefix = run_staging_prefix(run_id)
    store = FakeStore({
        f"{prefix}scene.json": json.dumps(fixed(draft())).encode(),
        f"{prefix}inputs/b1/manifest.json": b"{}",
    })
    writer = FakeWriter()

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=writer, store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert (result.itemized, result.failed) == (1, 0)
    assert writer.items[0]["properties"]["platform"] == "GOES-19"
    row = await ingest.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_ITEMIZED and row.item_id == "scene"
    assert ingest.flow_stats[assoc.id]["items"] == 1
    assert f"{prefix}scene.json" in store.deleted
    assert not process.finished  # a run that published is left `succeeded`


async def test_missing_or_bad_documents_fail_their_rows_with_a_reason():
    ingest, process, assoc, run_id = await _setup({}, rows=("a", "b", "c"))
    prefix = run_staging_prefix(run_id)
    tampered = fixed(draft("b"))
    tampered["id"] = "zzz"
    store = FakeStore({
        f"{prefix}a.json": b"not json",
        f"{prefix}b.json": json.dumps(tampered).encode(),
        # c.json absent
    })

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=FakeWriter(), store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert (result.itemized, result.failed) == (0, 3)
    for name in ("a", "b", "c"):
        row = await ingest.get_latest_ledger(assoc.id, f"{name}.nc")
        assert row.status == STATUS_FAILED and row.reason
    assert "no document" in (await ingest.get_latest_ledger(assoc.id, "c.nc")).reason
    assert ingest.flow_stats[assoc.id]["failed"] == 3
    # every output refused → the run is downgraded, mirroring the transform recorder
    assert process.finished[-1]["status"] == "dead"


async def test_extract_branch_is_idempotent_on_rows_that_moved_on():
    ingest, process, _assoc, run_id = await _setup({})
    (rid,) = [r.id for r in ingest.rows.values()]
    await ingest.set_ledger_status_many([rid], status=STATUS_ITEMIZED, item_id="scene")
    prefix = run_staging_prefix(run_id)
    store = FakeStore({f"{prefix}scene.json": json.dumps(fixed(draft())).encode()})
    writer = FakeWriter()

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=writer, store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert result.skipped == 1 and writer.items == []


async def test_extract_branch_fails_the_batch_when_the_association_is_gone():
    ingest, process, _assoc, run_id = await _setup({})
    ingest.associations.clear()
    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=FakeWriter(),
        store=FakeStore({}), adapter_for=lambda a: FakeAdapter(),
    )
    assert result.failed == 1
    row = next(iter(ingest.rows.values()))
    assert row.status == STATUS_FAILED and "association" in row.reason


async def test_extract_branch_fails_the_batch_when_the_run_lost_its_association():
    """Migration 027 is ON DELETE SET NULL, so a run can outlive its
    association: the rows must still be failed with a reason rather than left
    `extracting` for the stall sweep, and there is nothing left to bump."""
    ingest, process, _assoc, run_id = await _setup({})
    for row in process.enqueued:
        if row["run_id"] == run_id:
            row["association_id"] = None
    prefix = run_staging_prefix(run_id)
    store = FakeStore({f"{prefix}scene.json": json.dumps(fixed(draft())).encode()})

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=FakeWriter(), store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert result.failed == 1
    ledger_row = next(iter(ingest.rows.values()))
    assert ledger_row.status == STATUS_FAILED
    assert "deleted during extraction" in ledger_row.reason
    assert ingest.flow_stats == {}  # no association row left to bump
