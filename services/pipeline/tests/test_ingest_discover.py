"""DISCOVER stage: settled-check state machine + path normalization."""

from __future__ import annotations

from _ingest_fake import FakeAdapter, FakeIngestRepo
from pipeline.connections.adapters.base import FileEntry
from pipeline.connections.repo import ConnectionRow
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.discover import (
    discover_stage,
    fingerprint_of,
    relative_source_path,
    source_fetch_path,
)
from pipeline.ingest.repo import IngestAssociation


def _assoc(config: dict) -> IngestAssociation:
    conn = ConnectionRow(
        id="conn1", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    return IngestAssociation(
        id="assoc1",
        collection_id="sentinel-2",
        connection_id="conn1",
        config=config,
        connection=conn,
    )


def _entry(path, *, size=100, etag=None, mtime=1_700_000_000.0, is_dir=False):
    return FileEntry(path=path, size=size, etag=etag, mtime=mtime, is_dir=is_dir)


# --- path helpers ---------------------------------------------------------- #


def test_relative_source_path_strips_s3_style_prefix():
    assert relative_source_path("products/a.tif", "products/") == "a.tif"
    assert relative_source_path("products/sub/a.tif", "products") == "sub/a.tif"


def test_relative_source_path_leaves_bare_name():
    # SFTP/FTP already return names relative to the listed prefix.
    assert relative_source_path("scene.tif", "in") == "scene.tif"


def test_source_fetch_path_roundtrips_both_conventions():
    # s3 key reconstruction equals the original key.
    assert source_fetch_path("products/", "a.tif") == "products/a.tif"
    # sftp/ftp: root-relative path get() re-resolves under root_path.
    assert source_fetch_path("in", "scene.tif") == "in/scene.tif"


def test_fingerprint_prefers_etag_then_size_mtime():
    assert fingerprint_of(_entry("a", etag="abc")) == "abc"
    assert fingerprint_of(_entry("a", size=10, mtime=1234.9)) == "10:1234"
    assert fingerprint_of(_entry("a", size=10, mtime=None)) == "10:"
    assert fingerprint_of(_entry("a", size=None, mtime=None)) is None


# --- state machine --------------------------------------------------------- #


async def test_new_file_recorded_as_seen():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(entries=[_entry("products/a.tif", etag="v1")])
    # config is passed directly; the stage does not read association.config.
    result = await discover_stage(repo, _assoc({}), cfg, adapter)
    assert result.new_seen == 1
    row = await repo.get_latest_ledger("assoc1", "a.tif")
    assert row is not None
    assert row.status == "seen"
    assert row.fingerprint == "v1"
    assert row.version == 1


async def test_unchanged_second_poll_settles():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    adapter = FakeAdapter(entries=[_entry("products/a.tif", etag="v1")])
    await discover_stage(repo, _assoc({}), cfg, adapter)
    result = await discover_stage(repo, _assoc({}), cfg, adapter)
    assert result.settled == 1
    row = await repo.get_latest_ledger("assoc1", "a.tif")
    assert row.status == "settled"


async def test_changed_while_seen_resets_window():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    v1 = FakeAdapter(entries=[_entry("products/a.tif", etag="v1")])
    await discover_stage(repo, _assoc({}), cfg, v1)
    result = await discover_stage(
        repo, _assoc({}), cfg, FakeAdapter(entries=[_entry("products/a.tif", etag="v2")])
    )
    assert result.changed_while_seen == 1
    row = await repo.get_latest_ledger("assoc1", "a.tif")
    assert row.status == "seen"
    assert row.fingerprint == "v2"


async def test_settled_then_changed_reverts_to_seen():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    stable = FakeAdapter(entries=[_entry("products/a.tif", etag="v1")])
    await discover_stage(repo, _assoc({}), cfg, stable)
    await discover_stage(repo, _assoc({}), cfg, stable)  # → settled
    result = await discover_stage(
        repo, _assoc({}), cfg, FakeAdapter(entries=[_entry("products/a.tif", etag="v2")])
    )
    assert result.unsettled == 1
    row = await repo.get_latest_ledger("assoc1", "a.tif")
    assert row.status == "seen"


async def test_directories_and_filtered_files_skipped():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/", "include": ["**/*.tif"]})
    adapter = FakeAdapter(
        entries=[
            _entry("products/sub", is_dir=True),
            _entry("products/a.tif", etag="v1"),
            _entry("products/notes.txt", etag="t1"),
        ]
    )
    result = await discover_stage(repo, _assoc({}), cfg, adapter)
    assert result.new_seen == 1
    assert result.skipped == 1
    assert await repo.get_latest_ledger("assoc1", "notes.txt") is None


async def test_unfingerprintable_file_skipped():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "in"})
    adapter = FakeAdapter(entries=[_entry("scene.tif", size=None, mtime=None, etag=None)])
    result = await discover_stage(repo, _assoc({}), cfg, adapter)
    assert result.unfingerprinted == 1
    assert await repo.get_latest_ledger("assoc1", "scene.tif") is None


async def test_reingest_on_change_after_itemized():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    # seed an itemized v1
    eid = await repo.insert_ledger_version(
        "assoc1", "a.tif", version=1, status="itemized", size=100, fingerprint="v1", item_id="a"
    )
    await repo.set_ledger_fields(eid, status="itemized")
    result = await discover_stage(
        repo, _assoc({}), cfg, FakeAdapter(entries=[_entry("products/a.tif", etag="v2")])
    )
    assert result.reingest == 1
    row = await repo.get_latest_ledger("assoc1", "a.tif")
    assert row.version == 2
    assert row.status == "seen"
    assert row.item_id == "a"  # same product


async def test_itemized_unchanged_is_noop():
    repo = FakeIngestRepo()
    cfg = parse_ingest_config({"source_path": "products/"})
    await repo.insert_ledger_version(
        "assoc1", "a.tif", version=1, status="itemized", size=100, fingerprint="v1", item_id="a"
    )
    result = await discover_stage(
        repo, _assoc({}), cfg, FakeAdapter(entries=[_entry("products/a.tif", etag="v1")])
    )
    assert result.unchanged == 1
    assert (await repo.get_latest_ledger("assoc1", "a.tif")).version == 1
