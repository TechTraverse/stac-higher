"""ITEMIZE stage: validate + upsert + ledger + post-ingest (§6.1 tail)."""

from __future__ import annotations

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_bounds

from _ingest_fake import (
    FakeAdapter,
    FakeIngestRepo,
    FakeS3,
    FakeWriter,
    RaisingWriter,
    make_association,
)
from _process_fake import FakeProcessRepo
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.itemize import (
    ItemValidationError,
    complete_item,
    run_itemize,
    validate_item,
)
from pipeline.ingest.repo import (
    STATUS_EXTRACTING,
    STATUS_FAILED,
    STATUS_ITEMIZED,
    STATUS_STORED,
)

_assoc = make_association


def _geotiff_bytes():
    arr = np.arange(16, dtype="uint8").reshape(1, 4, 4)
    transform = from_bounds(-1, -1, 1, 1, 4, 4)
    with MemoryFile() as mem:
        with mem.open(
            driver="GTiff", height=4, width=4, count=1, dtype="uint8",
            crs="EPSG:4326", transform=transform,
        ) as ds:
            ds.write(arr)
        return mem.read()


def _valid_item():
    return {
        "type": "Feature",
        "stac_version": "1.0.0",
        "stac_extensions": [],
        "id": "scene",
        "collection": "col",
        "geometry": None,
        "properties": {"datetime": "2021-01-01T00:00:00Z"},
        "assets": {},
        "links": [],
    }


def test_validate_item_accepts_null_geometry():
    validate_item(_valid_item())  # no raise


def test_validate_item_rejects_missing_datetime():
    bad = _valid_item()
    bad["properties"] = {}
    with pytest.raises(ItemValidationError):
        validate_item(bad)


async def test_run_itemize_defaults_only_upserts_and_marks_itemized():
    # scene.bin is not a GDAL-candidate, so this opts into the collection-
    # extent fallback (ISSUE I-27) to reach a geometry; the default
    # `FakeWriter` returns no collection bbox, so it degrades to
    # `global_fallback` — this test only cares about the itemize/upsert path.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter()

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    assert writer.items and writer.items[0]["id"] == "scene"
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_ITEMIZED
    assert row.item_id == "scene"


async def test_run_itemize_collection_missing_marks_failed():
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )

    out = await run_itemize(
        repo,
        FakeWriter(raise_missing=True),
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=parse_ingest_config(assoc.config),
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert out.status == "failed"
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_FAILED
    # a failed upsert must not stamp item_id on the ledger row
    assert row.item_id is None


async def test_run_itemize_marks_all_members_atomically():
    # A two-member group (e.g. scene.tif + scene.xml) under a defaults_only
    # association: both members are `stored` and must end `itemized` with the
    # item_id set together, via a single set_ledger_status_many call — not two
    # independent set_ledger_fields calls that could leave the group split if
    # the process crashed between them.
    # Opts into the collection-extent fallback (ISSUE I-27): scene.tif is a
    # GDAL-candidate, but `FakeS3` here has no bytes for it (it only models
    # the FETCH-stage `put_object` calls), so best-effort recovery misses and
    # this falls through to the opted-in `global_fallback`.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.tif", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.xml", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter()

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.tif", "scene.xml"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    tif_row = await repo.get_latest_ledger(assoc.id, "scene.tif")
    xml_row = await repo.get_latest_ledger(assoc.id, "scene.xml")
    assert tif_row.status == STATUS_ITEMIZED
    assert tif_row.item_id == "scene"
    assert xml_row.status == STATUS_ITEMIZED
    assert xml_row.item_id == "scene"
    # exactly one atomic call, not one per member
    assert repo.set_ledger_status_many_calls == 1


async def test_run_itemize_skips_when_no_stored_members():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "/out"})
    writer = FakeWriter()

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=parse_ingest_config(assoc.config),
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert out.status == "skipped"
    # a skip must be a true no-op: the writer must never be invoked
    assert writer.items == []


async def test_run_itemize_propagates_unexpected_writer_error():
    # Same happy-path fixtures as
    # test_run_itemize_defaults_only_upserts_and_marks_itemized: EXTRACT and
    # validation succeed and control reaches the writer, but this time the
    # writer raises an unexpected (non-CollectionMissing) error simulating a
    # transient DB failure.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = RaisingWriter()

    with pytest.raises(RuntimeError):
        await run_itemize(
            repo,
            writer,
            FakeAdapter(),
            FakeS3(),
            association=assoc,
            config=config,
            item_id="scene",
            source_paths=["scene.bin"],
            bucket="b",
            asset_href_base="/api/assets",
        )

    # the ledger row must NOT be advanced past `stored` — a stray
    # `except Exception` here would silently swallow the error and lose the
    # item; leaving it at `stored` lets the job retry cleanly.
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_STORED


async def test_run_itemize_defaults_only_opted_in_uses_collection_extent():
    # `.bin` is not a GDAL-candidate, so only the opt-in collection-extent
    # fallback (ISSUE I-27) can recover a geometry here.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter(collection_bbox=[10, 20, 30, 40])

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    assert writer.get_collection_bbox_calls == ["col"]
    item = writer.items[0]
    assert item["geometry"] is not None
    assert item["bbox"] == [10, 20, 30, 40]
    assert item["properties"]["stac_higher:geometry_source"] == "collection_extent"
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_ITEMIZED


async def test_run_itemize_defaults_only_opted_in_no_collection_extent_uses_global():
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter(collection_bbox=None)

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    item = writer.items[0]
    assert item["properties"]["stac_higher:geometry_source"] == "global_fallback"
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_ITEMIZED


async def test_run_itemize_defaults_only_opted_in_6d_bbox_reduces_to_2d():
    # A 3D collection extent bbox (`[w, s, min_elev, e, n, max_elev]`, as
    # pgstac's `get_collection_bbox` faithfully returns for 3D spatial
    # extents) must reduce to the horizontal 2D extent `[w, s, e, n]` — NOT
    # a naive `bbox[:4]` slice, which mangles `min_elev` into "east" and
    # drops `north` entirely.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter(collection_bbox=[10, 20, 5, 30, 40, 100])

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    item = writer.items[0]
    assert item["bbox"] == [10, 20, 30, 40]
    assert item["properties"]["stac_higher:geometry_source"] == "collection_extent"
    assert item["geometry"]["coordinates"][0][0] == [10, 20]
    assert item["geometry"]["coordinates"][0][2] == [30, 40]
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_ITEMIZED


async def test_run_itemize_defaults_only_opted_in_4d_bbox_still_works():
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter(collection_bbox=[10, 20, 30, 40])

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    item = writer.items[0]
    assert item["bbox"] == [10, 20, 30, 40]
    assert item["properties"]["stac_higher:geometry_source"] == "collection_extent"


async def test_run_itemize_defaults_only_opted_in_6d_global_bbox_uses_global():
    # A 3D global extent (min/max elevation on top of the world horizontal
    # bbox) should still classify as `global_fallback` after normalization.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter(collection_bbox=[-180, -90, 0, 180, 90, 5000])

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    item = writer.items[0]
    assert item["properties"]["stac_higher:geometry_source"] == "global_fallback"


async def test_run_itemize_defaults_only_not_opted_in_fails_without_geometry():
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "metadata": {
                "strategy": "defaults_only",
                "defaults": {"datetime": "2021-01-01T00:00:00Z"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter()

    out = await run_itemize(
        repo,
        writer,
        FakeAdapter(),
        FakeS3(),
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.bin"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert out.status == "failed"
    assert writer.items == []
    assert writer.get_collection_bbox_calls == []
    row = await repo.get_latest_ledger(assoc.id, "scene.bin")
    assert row.status == STATUS_FAILED


async def test_run_itemize_reference_reads_source_via_adapter_and_marks_itemized():
    # Reference mode (Phase 4 Slice C): FETCH never copied bytes to canonical
    # storage, so EXTRACT must read the source raster in place via the
    # adapter (`SourceAdapterByteSource`) — the built item is otherwise
    # identical to the copy-mode round-trip
    # (test_run_itemize_defaults_only_upserts_and_marks_itemized), just with
    # `storage_mode: reference` and bytes staged in the adapter instead of
    # the canonical FakeS3.
    repo = FakeIngestRepo()
    assoc = _assoc(
        {
            "source_path": "/out",
            "storage_mode": "reference",
            "metadata": {"strategy": "raster_auto"},
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.tif", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = FakeWriter()
    adapter = FakeAdapter(blobs={"/out/scene.tif": _geotiff_bytes()})

    out = await run_itemize(
        repo,
        writer,
        adapter,
        FakeS3(),  # canonical storage — must never be read in reference mode
        association=assoc,
        config=config,
        item_id="scene",
        source_paths=["scene.tif"],
        bucket="b",
        asset_href_base="/api/assets",
    )

    assert (out.status, out.item_id) == ("itemized", "scene")
    assert adapter.get_calls == ["/out/scene.tif"]
    assert writer.items and writer.items[0]["id"] == "scene"
    item = writer.items[0]
    assert item["geometry"] is not None
    assert item["assets"]["scene"]["href"] == "/api/assets/col/scene/scene.tif"
    row = await repo.get_latest_ledger(assoc.id, "scene.tif")
    assert row.status == STATUS_ITEMIZED
    assert row.item_id == "scene"


# --- GOES G-6: the extractor strategy branch + the shared `complete_item` tail ---

PROC = "5c9f1c2e-0000-4000-8000-0000000000e1"


def _extractor_assoc():
    return _assoc(
        {
            "source_path": "/out",
            "metadata": {"strategy": "extractor", "extractor": {"process_id": PROC}},
        }
    )


async def test_extractor_strategy_parks_rows_and_triggers_the_run():
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    rid = await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    process_repo = FakeProcessRepo(kinds={PROC: "extractor"})
    enqueued: list[str] = []

    async def enqueue_now(run_id: str) -> None:
        enqueued.append(run_id)

    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=process_repo, enqueue_now=enqueue_now,
    )

    assert out.status == "extracting"
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_EXTRACTING
    assert row.extract_run_id == out.detail == enqueued[0]
    run = process_repo.enqueued[0]
    assert run["association_id"] == assoc.id and run["source_id"] is None
    (entry,) = run["input_items"]
    assert entry["item_id"] == "scene" and entry["collection_id"] == "col"
    assert entry["ledger_ids"] == [rid] and entry["op"] == "insert"
    assert entry["draft"]["id"] == "scene" and entry["draft"]["collection"] == "col"
    assert entry["draft"]["assets"]["scene"]["href"] == "/api/assets/col/scene/scene.nc"
    # The draft is best-effort: no datetime default, so it is left for the extractor.
    assert entry["draft"]["properties"]["datetime"] is None
    assert entry["draft"]["geometry"] is None


async def test_extractor_strategy_fails_rows_when_the_process_is_unusable():
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    # wrong kind
    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=FakeProcessRepo(kinds={PROC: "transform"}),
    )
    assert out.status == "failed" and "not an extractor" in out.detail
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_FAILED and "not an extractor" in row.reason

    # nothing deployed
    repo2 = FakeIngestRepo()
    await repo2.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    out = await run_itemize(
        repo2, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=FakeProcessRepo(kinds={PROC: "extractor"}, deployed_revision=None),
    )
    assert out.status == "failed" and "no deployed revision" in out.detail


async def test_extractor_strategy_fails_rows_without_a_process_repository():
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
    )
    assert out.status == "failed" and "no process repository" in out.detail
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_FAILED


async def test_complete_item_is_the_shared_tail():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "/out", "metadata": {"strategy": "defaults_only",
                    "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"}}})
    rid = await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_EXTRACTING, size=3, fingerprint="f"
    )
    rows = await repo.get_ledger_entries([rid])
    writer = FakeWriter()
    item = _valid_item()
    item["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    item["bbox"] = [0, 0, 0, 0]  # stac-pydantic: a non-null geometry needs a bbox

    out = await complete_item(
        repo, writer, FakeAdapter(), association=assoc,
        config=parse_ingest_config(assoc.config), item_id="scene", members=rows, item_dict=item,
    )

    assert out.status == "itemized" and out.bytes == 3
    assert writer.items == [item]
    assert repo.rows[rid].status == STATUS_ITEMIZED and repo.rows[rid].item_id == "scene"


async def test_parked_extracting_rows_keep_their_item_id():
    """Critical: the run planner resolves reference-mode inputs via
    `reference_source_hrefs(collection, item_id)`, which filters the ledger on
    `item_id`. Blanking it while the group is `extracting` makes every
    reference-mode input unresolvable on first ingest."""
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f",
        item_id="scene",
    )

    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=FakeProcessRepo(kinds={PROC: "extractor"}),
    )

    assert out.status == "extracting"
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_EXTRACTING
    assert row.item_id == "scene"


async def test_extractor_strategy_fails_rows_when_the_process_is_disabled():
    """A disabled extractor used to be reported as "no deployed revision"
    (`current_revision` filters on `enabled`), which sends an operator to the
    wrong screen."""
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    process_repo = FakeProcessRepo(kinds={PROC: "extractor"}, disabled_processes={PROC})

    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=process_repo,
    )

    assert out.status == "failed" and "is disabled" in out.detail
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_FAILED and "is disabled" in row.reason
