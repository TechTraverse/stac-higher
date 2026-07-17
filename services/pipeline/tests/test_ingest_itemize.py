"""ITEMIZE stage: validate + upsert + ledger + post-ingest (§6.1 tail)."""

from __future__ import annotations

import pytest

from _ingest_fake import FakeAdapter, FakeIngestRepo, FakeS3
from pipeline.connections.repo import ConnectionRow
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.itemize import (
    ItemizeOutcome,
    ItemValidationError,
    run_itemize,
    validate_item,
)
from pipeline.ingest.repo import (
    STATUS_FAILED,
    STATUS_ITEMIZED,
    STATUS_STORED,
    IngestAssociation,
)
from pipeline.stac.pgstac_writer import CollectionMissing, PgstacWriter


def _assoc(config: dict) -> IngestAssociation:
    # Mirrors tests/test_ingest_fetch.py::_assoc — build the association inline;
    # there is no `make_association` helper in _ingest_fake.py.
    conn = ConnectionRow(
        id="c1", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    return IngestAssociation(
        id="assoc1",
        collection_id="col",
        config=config,
        connection=conn,
    )


class _FakeWriter(PgstacWriter):
    def __init__(self, raise_missing: bool = False):
        self.items: list = []
        self.raise_missing = raise_missing

    async def upsert_items(self, items):
        if self.raise_missing:
            raise CollectionMissing("Collection col is not present in the database")
        self.items.extend(items)


class _RaisingWriter(PgstacWriter):
    """Writer whose upsert_items raises an unexpected (non-CollectionMissing)
    error, e.g. a transient DB connection failure."""

    def __init__(self):
        self.items: list = []

    async def upsert_items(self, items):
        raise RuntimeError("connection refused")


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
    writer = _FakeWriter()

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

    assert out == ItemizeOutcome("itemized", "scene")
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
        _FakeWriter(raise_missing=True),
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
        assoc.id, "scene.tif", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.xml", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = _FakeWriter()

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

    assert out == ItemizeOutcome("itemized", "scene")
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
    writer = _FakeWriter()

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
                "defaults": {"datetime": "2021-01-01T00:00:00Z"},
            },
        }
    )
    await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_STORED, size=1, fingerprint="f"
    )
    config = parse_ingest_config(assoc.config)
    writer = _RaisingWriter()

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
