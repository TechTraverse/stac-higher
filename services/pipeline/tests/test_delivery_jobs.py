import pytest

from pipeline.config import Settings
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.repo import DeliverTarget
from pipeline.jobs import dispatch
from pipeline.jobs.dispatch import JOB_DELIVER, JOB_DISPATCH_POLL
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
    assert JOB_DELIVER in queue.tasks


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
