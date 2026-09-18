"""FETCH stage: copy settled bytes into canonical storage, ledger → stored."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib

from _ingest_fake import FakeAdapter, FakeIngestRepo, FakeS3, make_association
from pipeline.connections.repo import ConnectionRow
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.fetch import fetch_stage
from pipeline.ingest.repo import IngestAssociation
from pipeline.ingest.transfer import TransferPolicy


def _assoc(config: dict) -> IngestAssociation:
    return make_association(config, collection_id="sentinel-2")


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
    # fetched via the reconstructed source path (source_path + relpath); the
    # default (non-copy) transfer policy streams through `open()`, not the
    # buffered `get()` (M3-C) — this assertion moved with that change.
    assert adapter.open_calls == ["products/scene.tif"]
    # written to the canonical key in the platform bucket. Streamed uploads
    # (M3-C) also carry a boto3 `Config` (TransferConfig) kwarg, so this
    # checks the fields that matter rather than exact dict equality.
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == "stac-higher"
    assert put["Key"] == "assets/sentinel-2/scene/scene.tif"
    assert put["Body"] == b"abc"
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


async def test_fetch_reference_mode_records_source_href_no_copy():
    # Reference mode (Slice C): no byte copy. FETCH just advances settled →
    # stored and records the stable source URL so EXTRACT/ITEMIZE can run.
    from pipeline.connections.adapters.s3 import S3Adapter

    repo = FakeIngestRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products", "storage_mode": "reference"})
    conn = ConnectionRow(
        id="c1",
        name="src",
        protocol="s3",
        config={
            "bucket": "src-bucket",
            "endpoint": "http://minio:9000",
            "force_path_style": True,
        },
        credentials=None,
        host_key=None,
    )
    assoc = IngestAssociation(id="assoc1", collection_id="sentinel-2", config={}, connection=conn)
    # A real S3Adapter provides public_object_url; FETCH must not call get/put on it.
    adapter = S3Adapter(conn.config, {"access_key_id": "k", "secret_access_key": "s"})
    s3 = FakeS3()

    stored = await fetch_stage(repo, assoc, cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"])

    assert stored == 1
    assert s3.puts == []  # no canonical copy
    row = await repo.get_latest_ledger("assoc1", "scene.tif")
    assert row.status == "stored"
    assert row.item_id == "scene"
    assert row.source_href == "http://minio:9000/src-bucket/products/scene.tif"


async def test_fetch_reference_mode_skips_non_settled_member_idempotent():
    repo = FakeIngestRepo()
    member = await _settled(repo, "scene.tif")
    await repo.set_ledger_fields(member.id, status="stored")
    cfg = parse_ingest_config({"source_path": "products", "storage_mode": "reference"})
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


async def test_fetch_streams_with_the_policy_chunking_and_records_sha256():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "a.bin", size=5000)
    adapter = FakeAdapter(blobs={"in/a.bin": b"y" * 5000})
    s3 = FakeS3()
    policy = TransferPolicy(server_side_copy=False, chunk_bytes=1024, concurrency=2)
    stored = await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["a.bin"],
        transfer=policy,
    )
    assert stored == 1
    put = s3.puts[0]
    assert put["Body"] == b"y" * 5000
    assert put["Config"].multipart_chunksize == 1024 and put["Config"].max_concurrency == 2
    latest = await repo.get_latest_ledger(assoc.id, "a.bin")
    assert latest.status == "stored" and latest.checksum == hashlib.sha256(b"y" * 5000).hexdigest()
    assert s3.copies == []


async def test_fetch_copies_server_side_when_the_policy_allows_and_reads_no_bytes():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "a.bin")
    adapter = FakeAdapter(blobs={"in/a.bin": b"zzz"}, copy_bucket="src")
    s3 = FakeS3()
    stored = await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["a.bin"],
        transfer=TransferPolicy(server_side_copy=True),
    )
    assert stored == 1
    assert s3.puts == []
    assert s3.copies[0]["CopySource"] == {"Bucket": "src", "Key": "in/a.bin"}
    assert s3.copies[0]["Bucket"] == "bucket"
    assert adapter.get_calls == [] and adapter.open_calls == []
    latest = await repo.get_latest_ledger(assoc.id, "a.bin")
    assert latest.status == "stored" and latest.checksum is None


async def test_fetch_falls_back_to_streaming_when_the_copy_fails(caplog):
    import logging

    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "a.bin")
    adapter = FakeAdapter(blobs={"in/a.bin": b"zzz"}, copy_bucket="src")
    s3 = FakeS3(fail_copy=True)
    cfg = parse_ingest_config(assoc.config)
    with caplog.at_level(logging.INFO, logger="pipeline.ingest.fetch"):
        stored = await fetch_stage(
            repo, assoc, cfg, adapter, s3, "bucket", "item-1", ["a.bin"],
            transfer=TransferPolicy(server_side_copy=True),
        )
    assert stored == 1
    assert s3.puts[0]["Body"] == b"zzz"
    latest = await repo.get_latest_ledger(assoc.id, "a.bin")
    assert latest.checksum == hashlib.sha256(b"zzz").hexdigest()
    # M3-C final review finding 4: the group-done record carries the
    # transfer mode that actually moved the bytes (a fallback here).
    done = next(r for r in caplog.records if r.msg == "ingest fetch group done")
    assert done.transfer == {"copy": 0, "stream": 0, "copy_fallback": 1}


async def test_transfer_close_error_never_masks_the_real_upload_error():
    # M3-C final review finding 8: getattr(body, "close", None) + a raising
    # close() used to replace the real upload exception in the `finally`.
    import pytest

    from pipeline.ingest.fetch import _transfer

    class _RaisingCloseBody:
        def read(self, n=-1):
            return b""

        def close(self):
            raise RuntimeError("close blew up")

    class _NoCopyAdapter:
        def copy_source(self, path):
            return None

        async def open(self, path):
            return _RaisingCloseBody()

    class _FailingS3:
        def upload_fileobj(self, Fileobj, Bucket, Key, **kwargs):
            raise ValueError("upload blew up")

    with pytest.raises(ValueError, match="upload blew up"):
        await _transfer(
            _NoCopyAdapter(), _FailingS3(), "bucket", "key", "path",
            TransferPolicy(server_side_copy=False),
        )


async def test_fetch_never_copies_from_an_adapter_without_a_copy_source():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "in/", "storage_mode": "copy"})
    await _settled(repo, "a.bin")
    adapter = FakeAdapter(blobs={"in/a.bin": b"zzz"})  # copy_bucket None -> copy_source() None
    s3 = FakeS3()
    await fetch_stage(
        repo, assoc, parse_ingest_config(assoc.config), adapter, s3, "bucket", "item-1", ["a.bin"],
        transfer=TransferPolicy(server_side_copy=True),
    )
    assert s3.copies == [] and s3.puts[0]["Body"] == b"zzz"


class _YieldingRepo(FakeIngestRepo):
    """Yields to the event loop between the read and the claim, the way a real
    round trip does — two concurrent FETCH calls interleave like two workers —
    and hands back a SNAPSHOT of the row, the way a real SELECT does. (The
    plain fake returns the live object, so a racer would see the other's
    write through it and the old read-then-write guard would look safe.)"""

    async def get_latest_ledger(self, association_id, source_path):
        latest = await super().get_latest_ledger(association_id, source_path)
        # Snapshot BEFORE yielding: the copy is what the SELECT returned, not
        # what the row looks like once the other racer has written to it.
        snapshot = None if latest is None else dataclasses.replace(latest)
        await asyncio.sleep(0)
        return snapshot


async def test_two_concurrent_fetches_store_the_row_once():
    """S-C's one real defect: the settled -> fetching move was a read-then-write
    guard, so two overlapping FETCH jobs both fetched and both bumped. The
    claim is now the guard (M3-D): exactly one wins the row."""
    repo = _YieldingRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(blobs={"products/scene.tif": b"abc"})
    s3 = FakeS3()

    stored = await asyncio.gather(
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
    )

    assert sorted(stored) == [0, 1]
    assert len(s3.puts) == 1
    assert adapter.open_calls == ["products/scene.tif"]
    (row,) = repo.rows.values()
    assert row.status == "stored"


async def test_two_concurrent_reference_fetches_store_the_row_once():
    repo = _YieldingRepo()
    await _settled(repo, "scene.tif")
    cfg = parse_ingest_config({"source_path": "products/", "storage_mode": "reference"})
    adapter = FakeAdapter(blobs={"products/scene.tif": b"abc"})
    s3 = FakeS3()

    stored = await asyncio.gather(
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
        fetch_stage(repo, _assoc({}), cfg, adapter, s3, "stac-higher", "scene", ["scene.tif"]),
    )

    assert sorted(stored) == [0, 1]
    (row,) = repo.rows.values()
    assert row.status == "stored"
    assert row.item_id == "scene"


async def test_transition_ledger_is_a_no_op_when_the_row_moved_on():
    repo = FakeIngestRepo()
    row = await _settled(repo, "scene.tif")
    assert await repo.transition_ledger(
        row.id, expected_status="settled", status="fetching", item_id="scene"
    )
    assert row.status == "fetching" and row.item_id == "scene"
    assert not await repo.transition_ledger(
        row.id, expected_status="settled", status="fetching", item_id="other"
    )
    assert row.item_id == "scene"
