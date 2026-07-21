"""FETCH stage: copy settled bytes into canonical storage, ledger → stored."""

from __future__ import annotations

import hashlib

from _ingest_fake import FakeAdapter, FakeIngestRepo, FakeS3
from pipeline.connections.repo import ConnectionRow
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.fetch import fetch_stage
from pipeline.ingest.repo import IngestAssociation


def _assoc(config: dict) -> IngestAssociation:
    conn = ConnectionRow(
        id="c1", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    return IngestAssociation(
        id="assoc1",
        collection_id="sentinel-2",
        config=config,
        connection=conn,
    )


async def _settled(repo, source_path, size=3):
    eid = await repo.insert_ledger_version(
        "assoc1", source_path, version=1, status="settled", size=size, fingerprint="fp"
    )
    return repo.rows[eid]


async def test_fetch_copies_and_marks_stored():
    repo = FakeIngestRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(blobs={"products/scene.tif": b"abc"})
    s3 = FakeS3()

    stored = await fetch_stage(
        repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]
    )

    assert stored == 1
    # fetched via the reconstructed source path (source_path + relpath)
    assert adapter.get_calls == ["products/scene.tif"]
    # written to the canonical key in the platform bucket
    assert s3.puts == [
        {
            "Bucket": "stac-higher",
            "Key": "assets/sentinel-2/scene/scene.tif",
            "Body": b"abc",
        }
    ]
    row = await repo.get_latest_ledger("assoc1", "scene.tif")
    assert row.status == "stored"
    assert row.item_id == "scene"
    assert row.checksum == hashlib.sha256(b"abc").hexdigest()


async def test_fetch_groups_multiple_assets_under_one_item():
    repo = FakeIngestRepo()
    await _settled(repo, "scene.tif")
    await _settled(repo, "scene.xml")
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(
        blobs={"products/scene.tif": b"tif", "products/scene.xml": b"<x/>"}
    )
    s3 = FakeS3()

    stored = await fetch_stage(
        repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif", "scene.xml"]
    )

    assert stored == 2
    keys = sorted(p["Key"] for p in s3.puts)
    assert keys == [
        "assets/sentinel-2/scene/scene.tif",
        "assets/sentinel-2/scene/scene.xml",
    ]


async def test_fetch_skips_non_settled_member_idempotent():
    repo = FakeIngestRepo()
    member = await _settled(repo, "scene.tif")
    # simulate an already-stored row (a prior FETCH already ran)
    await repo.set_ledger_fields(member.id, status="stored")
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(blobs={"products/scene.tif": b"abc"})
    s3 = FakeS3()

    stored = await fetch_stage(
        repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]
    )
    assert stored == 0
    assert s3.puts == []


async def test_fetch_marks_failed_on_adapter_error():
    repo = FakeIngestRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(blobs={})  # get() raises KeyError → failure path
    s3 = FakeS3()

    stored = await fetch_stage(
        repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]
    )
    assert stored == 0
    assert (await repo.get_latest_ledger("assoc1", "scene.tif")).status == "failed"


async def test_reference_mode_skips_copy():
    repo = FakeIngestRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/", "storage_mode": "reference"})
    s3 = FakeS3()
    stored = await fetch_stage(
        repo, _assoc({}), cfg, FakeAdapter(), s3, "stac-higher", "scene", ["scene.tif"]
    )
    assert stored == 0
    assert s3.puts == []


async def test_set_ledger_fields_accepts_source_href_on_fake():
    # NOTE: this file's test module has no `tests` package (no __init__.py);
    # pytest's rootdir-insertion import mode puts `tests/` itself on
    # sys.path, so sibling modules import bare (`_ingest_fake`, matching the
    # module-level import above), not as `tests._ingest_fake`. The brief's
    # `from tests._ingest_fake import ...` doesn't resolve here.
    from pipeline.ingest.repo import LedgerEntry

    repo = FakeIngestRepo()
    repo.rows["1"] = LedgerEntry(
        id="1", association_id="a", source_path="products/scene.tif",
        version=1, size=10, fingerprint="f", checksum=None,
        status="settled", item_id=None,
    )
    # Guards the dataclass field itself, not just setattr: a freshly built
    # LedgerEntry with no source_href kwarg must default it to None. Before
    # the field exists, this raises AttributeError (dataclasses without
    # __slots__ let bare setattr create ad-hoc attributes, which would let
    # the round-trip below pass even without the field — so assert this too).
    assert repo.rows["1"].source_href is None
    await repo.set_ledger_fields("1", source_href="https://src/scene.tif")
    assert repo.rows["1"].source_href == "https://src/scene.tif"
