"""M2-0: no delivery may be lost before `deliver_item` records anything.

Two halves (spec §9 M2-0, TODO carried-forward follow-up):

1. **Pre-record** — the deliver handler inserts a placeholder `delivery_log`
   row for every item BEFORE the fallible `load_target` / `build_adapter` /
   `parse_delivery_config` / `get_item` work, so a failure there leaves a row
   the recovery sweeps can see instead of nothing at all. The placeholder must
   never disturb an existing row (the `on_update: ignore` fire-once gate and
   the log-based overwrite gate both read the prior row's state).
2. **Stall sweep** — rows stranded in `pending` / `delivering` (the job died
   between pre-record and delivery, or a worker crashed mid-transfer) re-enter
   the retry path, mirroring the ingest stored-stall sweep (I-52 / I-55).
"""

from __future__ import annotations

import datetime as dt

import pytest

from _delivery_fake import FakeDeliveryRepo
from pipeline.config import Settings
from pipeline.connections.build import AdapterBuildError
from pipeline.connections.repo import ConnectionRow
from pipeline.delivery.repo import DeliverTarget
from pipeline.jobs import dispatch
from pipeline.jobs.dispatch import JOB_DELIVER, JOB_RETRY_SWEEP
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio


def _target() -> DeliverTarget:
    return DeliverTarget(
        id="a1",
        collection_id="col",
        config={"path_template": "{filename}"},
        connection=ConnectionRow(
            id="c1",
            name="dest",
            protocol="s3",
            config={"bucket": "dest", "endpoint": "http://minio:9000"},
            credentials=None,
            host_key=None,
        ),
    )


def _wire(monkeypatch, fake: FakeDeliveryRepo, settings: Settings | None = None):
    """Register the dispatch jobs against a FakeDeliveryRepo."""
    queue = InMemoryQueue()
    settings = settings or Settings.from_env(env={})
    dispatch.register(queue, settings)
    monkeypatch.setattr(dispatch, "load_key_or_skip", lambda _s, _j: b"key")
    monkeypatch.setattr(dispatch, "PgDeliveryRepo", lambda _url: fake)
    monkeypatch.setattr(dispatch, "build_adapter", lambda *_a, **_k: object())
    monkeypatch.setattr(dispatch, "build_platform_client", lambda _s: object())
    return queue


def _items(*item_ids: str) -> list[dict]:
    return [
        {"item_id": i, "asset_keys": ["data"], "item_created_at": None}
        for i in item_ids
    ]


# --------------------------------------------------------------------------- #
# 1. pre-record
# --------------------------------------------------------------------------- #


async def test_pre_records_every_item_when_load_target_raises(monkeypatch):
    """The loss window: load_target fails, so deliver_item never runs. Without a
    pre-record there is no delivery_log row and the retry sweep has nothing to
    re-drive — the delivery vanishes once the queue retries are spent."""
    fake = FakeDeliveryRepo()

    async def _boom(_aid):
        raise RuntimeError("db unavailable")

    fake.load_target = _boom
    queue = _wire(monkeypatch, fake)

    with pytest.raises(RuntimeError):
        await queue.tasks[JOB_DELIVER](association_id="a1", items=_items("i1", "i2"))

    assert {r["item_id"] for r in fake.rows.values()} == {"i1", "i2"}
    assert {r["status"] for r in fake.rows.values()} == {"pending"}


async def test_pre_record_leaves_a_settled_row_untouched(monkeypatch):
    """The placeholder must be INSERT-only: resetting a delivered row to pending
    would clobber the state the on_update/overwrite gates read."""
    fake = FakeDeliveryRepo()
    row_id = await fake.upsert_pending("a1", "i1", None)
    await fake.mark_delivered(row_id, 42, {"data": {"fingerprint": "sha256:x"}})

    async def _boom(_aid):
        raise RuntimeError("db unavailable")

    fake.load_target = _boom
    queue = _wire(monkeypatch, fake)

    with pytest.raises(RuntimeError):
        await queue.tasks[JOB_DELIVER](association_id="a1", items=_items("i1"))

    assert fake.rows[row_id]["status"] == "delivered"
    assert fake.rows[row_id]["delivered_assets"] == {"data": {"fingerprint": "sha256:x"}}


async def test_discards_only_the_placeholders_it_created_when_target_is_gone(
    monkeypatch,
):
    """A disabled/deleted association is a legitimate no-op, not a failure — it
    must not leave phantom pending rows for the stall sweep to churn on. Rows
    that already existed are history and stay."""
    fake = FakeDeliveryRepo()
    kept = await fake.upsert_pending("a1", "i1", None)
    await fake.mark_delivered(kept, 42, {})
    fake.targets.clear()  # load_target -> None
    queue = _wire(monkeypatch, fake)

    await queue.tasks[JOB_DELIVER](association_id="a1", items=_items("i1", "i2"))

    assert list(fake.rows) == [kept]
    assert fake.rows[kept]["status"] == "delivered"


async def test_records_the_failure_when_the_adapter_cannot_be_built(monkeypatch):
    """An unbuildable destination adapter used to log and return — the batch
    disappeared with no trace in delivery_log. It must land as a failed row
    carrying the cause, on the normal retry schedule."""
    fake = FakeDeliveryRepo()
    fake.targets["a1"] = _target()
    queue = _wire(monkeypatch, fake)

    def _raise(*_a, **_k):
        raise AdapterBuildError("bad credentials")

    monkeypatch.setattr(dispatch, "build_adapter", _raise)

    await queue.tasks[JOB_DELIVER](association_id="a1", items=_items("i1"))

    (row,) = fake.rows.values()
    assert row["status"] == "failed"
    assert "bad credentials" in row["error"]
    assert row["next_attempt_at"] is not None


# --------------------------------------------------------------------------- #
# 2. stall sweep
# --------------------------------------------------------------------------- #


async def _stalled_row(fake: FakeDeliveryRepo, status: str) -> str:
    row_id = await fake.upsert_pending("a1", "i1", None)
    if status == "delivering":
        await fake.mark_delivering(row_id)
    fake.rows[row_id]["updated_at"] = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    return row_id


@pytest.mark.parametrize("status", ["pending", "delivering"])
async def test_retry_sweep_recovers_a_stalled_row(monkeypatch, status):
    """A row stranded mid-flight (job died after pre-record, or a worker crashed
    mid-transfer) re-enters the retry path on the sweep tick."""
    fake = FakeDeliveryRepo()
    row_id = await _stalled_row(fake, status)
    queue = _wire(monkeypatch, fake)

    await queue.run_periodic(JOB_RETRY_SWEEP, 0)

    requeued = [j for j in queue.jobs if j.name == JOB_DELIVER]
    assert requeued, "the recovered row should be re-enqueued"
    assert requeued[0].payload["items"][0]["item_id"] == "i1"
    assert fake.rows[row_id]["status"] == "pending"


async def test_stall_sweep_leaves_a_fresh_in_flight_row_alone(monkeypatch):
    """A delivery that is merely in progress must not be yanked out from under
    the worker — only rows past the stall window are presumed crashed."""
    fake = FakeDeliveryRepo()
    row_id = await fake.upsert_pending("a1", "i1", None)
    await fake.mark_delivering(row_id)
    queue = _wire(monkeypatch, fake)

    await queue.run_periodic(JOB_RETRY_SWEEP, 0)

    assert fake.rows[row_id]["status"] == "delivering"
    assert not [j for j in queue.jobs if j.name == JOB_DELIVER]


async def test_stall_window_is_configurable(monkeypatch):
    """Operators tune the window per deployment (long SFTP transfers need a
    wider one than the 30-minute default)."""
    settings = Settings.from_env(env={"DELIVERY_STALL_SECONDS": "60"})
    assert settings.delivery_stall_seconds == 60
