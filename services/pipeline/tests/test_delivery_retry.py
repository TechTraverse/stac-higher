"""Slice B-iii: retry → dead-letter in deliver_item, plus the I-49 ride-along
behaviors (partial-fingerprint retention, manifest pruning, source-read
failure, mixed reference+canonical, md5 + copy-failure fallback)."""

import hashlib
import json

import pytest

from _delivery_fake import FakeDeliveryRepo
from _ingest_fake import FakeAdapter
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.config import parse_delivery_config
from pipeline.delivery.repo import DeliverTarget, ReferenceSource
from pipeline.delivery.worker import (
    RETRY_BASE_SECONDS,
    RETRY_CAP_SECONDS,
    deliver_item,
    retry_delay_seconds,
)
from test_delivery_worker import _FakeAdapter, _FakeS3

pytestmark = pytest.mark.asyncio


def _target():
    return DeliverTarget(id="a1", collection_id="col", config={}, connection=None)


def _item(assets):
    return {"id": "scene", "collection": "col", "properties": {}, "assets": assets}


def _config(**overrides):
    base = {"path_template": "{filename}"}
    base.update(overrides)
    return parse_delivery_config(base)


async def _run(repo, adapter, s3, item, config, asset_keys=("data",), **kwargs):
    await deliver_item(
        repo, adapter, s3, "bucket",
        target=_target(), config=config, item=item,
        asset_keys=list(asset_keys), item_created_at=None,
        **kwargs,
    )


def test_retry_delay_exponential_doubles_and_caps():
    assert retry_delay_seconds("exponential", 1) == RETRY_BASE_SECONDS
    assert retry_delay_seconds("exponential", 2) == RETRY_BASE_SECONDS * 2
    assert retry_delay_seconds("exponential", 3) == RETRY_BASE_SECONDS * 4
    assert retry_delay_seconds("exponential", 99) == RETRY_CAP_SECONDS


def test_retry_delay_fixed_is_constant():
    assert retry_delay_seconds("fixed", 1) == RETRY_BASE_SECONDS
    assert retry_delay_seconds("fixed", 7) == RETRY_BASE_SECONDS


async def test_failure_schedules_retry():
    repo = FakeDeliveryRepo()
    s3 = _FakeS3({})  # canonical object missing -> failure
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(repo, _FakeAdapter(), s3, item, _config())
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    assert rec["next_attempt_at"] is not None
    assert rec["attempts"] == 1


async def test_dead_letter_at_max_attempts():
    repo = FakeDeliveryRepo()
    s3 = _FakeS3({})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(retry={"max_attempts": 2})

    await _run(repo, _FakeAdapter(), s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"

    # Simulate the sweep requeue, then fail again — budget exhausted -> dead.
    await repo.requeue_for_retry(list(repo.rows))
    await _run(repo, _FakeAdapter(), s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "dead"
    assert rec["next_attempt_at"] is None
    assert rec["attempts"] == 2


async def test_retry_does_not_reset_attempts_but_new_event_does():
    repo = FakeDeliveryRepo()
    s3 = _FakeS3({})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(retry={"max_attempts": 5})

    await _run(repo, _FakeAdapter(), s3, item, config)
    await repo.requeue_for_retry(list(repo.rows))
    await _run(repo, _FakeAdapter(), s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["attempts"] == 2  # requeued pending row keeps its count

    # Deliver successfully, then a NEW event starts a fresh cycle (I-44).
    s3.objects[("bucket", "assets/col/scene/a.tif")] = b"OK"
    await repo.requeue_for_retry(list(repo.rows))
    await _run(repo, _FakeAdapter(), s3, item, config)
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    await _run(repo, _FakeAdapter(), s3, item, config)  # new event, fresh cycle
    (rec,) = repo.rows.values()
    assert rec["attempts"] == 1


async def test_mid_batch_failure_keeps_partial_delivered_map():
    repo = FakeDeliveryRepo()

    class _FailSecond(_FakeAdapter):
        async def put_atomic(self, path, data):
            if path.startswith("b"):
                raise RuntimeError("dest died mid-batch")
            await super().put_atomic(path, data)

    s3 = _FakeS3({
        ("bucket", "assets/col/scene/a.tif"): b"AAA",
        ("bucket", "assets/col/scene/b.tif"): b"BBB",
    })
    item = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })

    await _run(repo, _FailSecond(), s3, item, _config(), asset_keys=("a", "b"))
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    # I-49: the already-delivered asset's fingerprint survives the failure, so
    # the retry's if_newer gate skips rewriting it.
    assert "a" in rec["delivered_assets"]
    assert "b" not in rec["delivered_assets"]

    adapter = _FakeAdapter()
    await repo.requeue_for_retry(list(repo.rows))
    await _run(repo, adapter, s3, item, _config(), asset_keys=("a", "b"))
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"
    assert [p for p, _ in adapter.puts] == ["b.tif"]  # 'a' skipped, unchanged


async def test_completion_manifest_prunes_assets_no_longer_in_item():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter()
    s3 = _FakeS3({
        ("bucket", "assets/col/scene/a.tif"): b"AAA",
        ("bucket", "assets/col/scene/b.tif"): b"BBB",
    })
    both = _item({
        "a": {"href": "/api/assets/col/scene/a.tif"},
        "b": {"href": "/api/assets/col/scene/b.tif"},
    })
    config = _config(
        payload={"item_json": True, "checksums": None, "completion_marker": True}
    )
    await _run(repo, adapter, s3, both, config, asset_keys=("a", "b"))
    manifest = json.loads(dict(adapter.puts)["scene.done"])
    assert {e["key"] for e in manifest["assets"]} == {"a", "b"}

    # Asset 'b' removed from the item: the next manifest must not list it.
    only_a = _item({"a": {"href": "/api/assets/col/scene/a.tif"}})
    await _run(repo, adapter, s3, only_a, config, asset_keys=("a",))
    manifest = json.loads(dict(adapter.puts)["scene.done"])
    assert {e["key"] for e in manifest["assets"]} == {"a"}


async def test_reference_source_read_failure_marks_failed():
    class _BoomSource(FakeAdapter):
        async def get(self, path):
            raise RuntimeError("source unreachable")

    conn = ConnectionRow(
        id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    src = ReferenceSource(filename="a.tif", fetch_path="in/a.tif", connection=conn)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(
        repo, _FakeAdapter(), _FakeS3({}), item, _config(),
        build_source_adapter=lambda c: _BoomSource(),
    )
    (rec,) = repo.rows.values()
    assert rec["status"] == "failed"
    assert "source unreachable" in rec["error"]


async def test_mixed_reference_and_canonical_assets_deliver_from_each():
    conn = ConnectionRow(
        id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    src = ReferenceSource(filename="ref.tif", fetch_path="in/ref.tif", connection=conn)
    repo = FakeDeliveryRepo(reference_sources={"scene": [src]})
    adapter = _FakeAdapter()
    s3 = _FakeS3({("bucket", "assets/col/scene/can.tif"): b"CANONICAL"})
    source = FakeAdapter(blobs={"in/ref.tif": b"REFERENCE"})
    item = _item({
        "r": {"href": "/api/assets/col/scene/ref.tif"},
        "c": {"href": "/api/assets/col/scene/can.tif"},
    })

    await _run(
        repo, adapter, s3, item, _config(),
        asset_keys=("r", "c"), build_source_adapter=lambda c: source,
    )
    assert dict(adapter.puts) == {"ref.tif": b"REFERENCE", "can.tif": b"CANONICAL"}
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_md5_checksums_with_copy_failure_falls_back_and_writes_sidecar():
    repo = FakeDeliveryRepo()
    adapter = _FakeAdapter(copy_error=RuntimeError("AccessDenied"))
    s3 = _FakeS3({("bucket", "assets/col/scene/a.tif"): b"IMGDATA"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})
    config = _config(
        payload={"item_json": False, "checksums": "md5", "completion_marker": False}
    )

    await _run(repo, adapter, s3, item, config, server_side_copy=True)
    puts = dict(adapter.puts)
    assert puts["a.tif"] == b"IMGDATA"  # streamed fallback
    assert puts["a.tif.md5"] == (
        f"{hashlib.md5(b'IMGDATA').hexdigest()}  a.tif\n".encode()
    )
    (rec,) = repo.rows.values()
    assert rec["status"] == "delivered"


async def test_reference_basename_collision_keeps_first_source():
    conn = ConnectionRow(
        id="c9", name="src", protocol="s3", config={}, credentials=None, host_key=None
    )
    sources = [
        ReferenceSource(filename="a.tif", fetch_path="one/a.tif", connection=conn),
        ReferenceSource(filename="a.tif", fetch_path="two/a.tif", connection=conn),
    ]
    repo = FakeDeliveryRepo(reference_sources={"scene": sources})
    adapter = _FakeAdapter()
    source = FakeAdapter(blobs={"one/a.tif": b"FIRST", "two/a.tif": b"SECOND"})
    item = _item({"data": {"href": "/api/assets/col/scene/a.tif"}})

    await _run(
        repo, adapter, _FakeS3({}), item, _config(),
        build_source_adapter=lambda c: source,
    )
    assert adapter.puts == [("a.tif", b"FIRST")]
