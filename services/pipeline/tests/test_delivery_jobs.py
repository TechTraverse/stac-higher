import datetime as dt

import pytest

from _delivery_fake import FakeDeliveryRepo
from pipeline.config import Settings
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.repo import DeliverTarget
from pipeline.jobs import dispatch
from pipeline.jobs.dispatch import (
    JOB_DELIVER,
    JOB_DISPATCH_POLL,
    JOB_RETRY_SWEEP,
    _entry_asset_keys,
)
from pipeline.main import build_queue
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio


def _s3_connection(endpoint):
    return ConnectionRow(
        id="c1",
        name="dest",
        protocol="s3",
        config={"bucket": "dest", "endpoint": endpoint},
        credentials=None,
        host_key=None,
    )


def test_register_wires_dispatch_poll_and_deliver_task():
    queue = InMemoryQueue()
    dispatch.register(queue, Settings.from_env(env={}))
    assert JOB_DISPATCH_POLL in queue.periodic
    assert JOB_RETRY_SWEEP in queue.periodic
    assert JOB_DELIVER in queue.tasks
    # I-55: a transient failure before deliver_item records anything would
    # otherwise lose the delivery (no delivery_log row for the sweep to see)
    assert JOB_DELIVER in queue.retry_specs


def test_build_queue_includes_deliver_task():
    queue = build_queue(Settings.from_env(env={}))
    assert JOB_DELIVER in set(queue.app.tasks)


async def test_deliver_handler_calls_worker_per_item(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    dispatch.register(queue, settings)

    target = DeliverTarget(
        id="a1",
        collection_id="col",
        config={"path_template": "{filename}"},
        connection=_s3_connection("http://minio:9000"),
    )

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return target
        async def get_item(self, _c, item_id):
            return {"id": item_id, "collection": "col", "properties": {}, "assets": {}}

    calls: list[str] = []

    async def _fake_deliver_item(
        _repo, _adapter, _s3, _bucket, *, target, config, item, asset_keys,
        item_created_at, **kwargs,
    ):
        calls.append(item["id"])

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(dispatch, "deliver_item", _fake_deliver_item)

    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[
            {"item_id": "i1", "asset_keys": ["data"], "item_created_at": None},
            {"item_id": "i2", "asset_keys": ["data"], "item_created_at": None},
        ],
    )
    assert calls == ["i1", "i2"]


async def test_deliver_handler_noops_when_target_gone(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    dispatch.register(queue, settings)

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return None  # disabled/deleted between dispatch and delivery

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)

    # must not raise
    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[{"item_id": "i1", "asset_keys": [], "item_created_at": None}],
    )


async def _run_deliver_capturing(monkeypatch, connection, settings):
    queue = InMemoryQueue()
    dispatch.register(queue, settings)
    target = DeliverTarget(
        id="a1", collection_id="col",
        config={"path_template": "{filename}"}, connection=connection,
    )

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return target
        async def get_item(self, _c, item_id):
            return {"id": item_id, "collection": "col", "properties": {}, "assets": {}}

    captured: dict = {}

    async def _fake_deliver_item(_repo, _adapter, _s3, _bucket, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(dispatch, "deliver_item", _fake_deliver_item)
    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[{"item_id": "i1", "asset_keys": ["data"], "item_created_at": None}],
    )
    return captured


async def test_deliver_passes_copy_gate_when_endpoints_match(monkeypatch):
    settings = Settings.from_env(env={})
    captured = await _run_deliver_capturing(
        monkeypatch, _s3_connection(settings.staging_s3_endpoint), settings
    )
    assert captured["server_side_copy"] is True
    assert callable(captured["build_source_adapter"])


async def test_deliver_copy_gate_false_on_foreign_endpoint(monkeypatch):
    settings = Settings.from_env(env={})
    captured = await _run_deliver_capturing(
        monkeypatch, _s3_connection("http://elsewhere:9000"), settings
    )
    assert captured["server_side_copy"] is False


# --------------------------------------------------------------------------- #
# B-iii: retry sweep + concurrency
# --------------------------------------------------------------------------- #


def test_entry_asset_keys_passthrough_and_rederive():
    item = {"assets": {"a": {}, "b": {}}}
    # dispatch-time entries pass through untouched
    assert _entry_asset_keys(["a"], item, None) == ["a"]
    # sweep entries (None) re-derive: config null = all current assets
    assert _entry_asset_keys(None, item, None) == ["a", "b"]
    # config asset_keys intersect the item's current assets
    assert _entry_asset_keys(None, item, ("b", "missing")) == ["b"]


async def test_retry_sweep_requeues_and_enqueues_batches(monkeypatch):
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    dispatch.register(queue, settings)

    repo = FakeDeliveryRepo()
    past = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    for rid, (assoc, item) in enumerate(
        [("a1", "i1"), ("a1", "i2"), ("a2", "i3")], start=1
    ):
        repo.rows[f"row{rid}"] = {
            "association_id": assoc,
            "item_id": item,
            "item_created_at": None,
            "status": "failed",
            "attempts": 1,
            "bytes": None,
            "error": "boom",
            "delivered_assets": {},
            "next_attempt_at": past,
        }
    # a dead row must never be swept
    repo.rows["dead"] = dict(
        repo.rows["row1"], item_id="i9", status="dead", next_attempt_at=None
    )
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", lambda _url: repo)

    await queue.periodic[JOB_RETRY_SWEEP].func(timestamp=0)

    # due rows flipped to pending so the next tick cannot re-enqueue them
    assert all(
        rec["status"] == "pending"
        for rid, rec in repo.rows.items()
        if rid.startswith("row")
    )
    assert repo.rows["dead"]["status"] == "dead"
    batches = {
        job.payload["association_id"]: job.payload["items"] for job in queue.jobs
    }
    assert {i["item_id"] for i in batches["a1"]} == {"i1", "i2"}
    assert [i["item_id"] for i in batches["a2"]] == ["i3"]
    # sweep entries carry no stale asset keys — the deliver job re-derives
    assert all(i["asset_keys"] is None for b in batches.values() for i in b)


async def test_retry_sweep_noop_when_nothing_due(monkeypatch):
    queue = InMemoryQueue()
    dispatch.register(queue, Settings.from_env(env={}))
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", lambda _url: FakeDeliveryRepo())
    await queue.periodic[JOB_RETRY_SWEEP].func(timestamp=0)
    assert queue.jobs == []


async def test_deliver_runs_items_concurrently_for_s3(monkeypatch):
    """cap > 1 on an S3 destination takes the gather path — every item still
    delivers exactly once."""
    queue = InMemoryQueue()
    settings = Settings.from_env(env={})
    dispatch.register(queue, settings)
    target = DeliverTarget(
        id="a1",
        collection_id="col",
        config={"path_template": "{filename}", "max_concurrent_transfers": 3},
        connection=_s3_connection("http://minio:9000"),
    )

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return target
        async def get_item(self, _c, item_id):
            return {
                "id": item_id,
                "collection": "col",
                "properties": {},
                "assets": {"data": {"href": f"/api/assets/col/{item_id}/a.tif"}},
            }

    delivered: list[str] = []

    async def _fake_deliver_item(_repo, _adapter, _s3, _bucket, *, item, **kwargs):
        delivered.append(item["id"])

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(dispatch, "deliver_item", _fake_deliver_item)

    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[
            {"item_id": f"i{n}", "asset_keys": ["data"], "item_created_at": None}
            for n in range(5)
        ],
    )
    assert sorted(delivered) == [f"i{n}" for n in range(5)]


async def test_deliver_sftp_destination_runs_serial(monkeypatch):
    """Non-S3 destinations ignore the cap — single-channel clients are not
    concurrency-safe, so items run strictly in order."""
    queue = InMemoryQueue()
    dispatch.register(queue, Settings.from_env(env={}))
    conn = ConnectionRow(
        id="c1", name="dest", protocol="sftp",
        config={"host": "sftp-test"}, credentials=None, host_key=None,
    )
    target = DeliverTarget(
        id="a1",
        collection_id="col",
        config={"path_template": "{filename}", "max_concurrent_transfers": 8},
        connection=conn,
    )

    class _Repo:
        def __init__(self, _url): ...
        async def load_target(self, _aid):
            return target
        async def get_item(self, _c, item_id):
            return {
                "id": item_id,
                "collection": "col",
                "properties": {},
                "assets": {"data": {"href": f"/api/assets/col/{item_id}/a.tif"}},
            }

    order: list[str] = []

    async def _fake_deliver_item(_repo, _adapter, _s3, _bucket, *, item, **kwargs):
        order.append(f"start:{item['id']}")
        order.append(f"end:{item['id']}")

    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", _Repo)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    monkeypatch.setattr(dispatch, "deliver_item", _fake_deliver_item)

    await queue.tasks[JOB_DELIVER](
        association_id="a1",
        items=[
            {"item_id": "i1", "asset_keys": ["data"], "item_created_at": None},
            {"item_id": "i2", "asset_keys": ["data"], "item_created_at": None},
        ],
    )
    assert order == ["start:i1", "end:i1", "start:i2", "end:i2"]
