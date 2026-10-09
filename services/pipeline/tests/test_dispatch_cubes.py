"""Z-3 dispatcher matching (virtual cube spec §5.2)."""

import datetime as dt

import pytest

from _cube_fake import FakeCubeRepo, FakeSink
from _dispatch_fake import FakeDispatchRepo
from pipeline.config import Settings
from pipeline.cubes.repo import REASON_NO_DATETIME
from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.loop import dispatch_once, dispatch_until_empty
from pipeline.dispatcher.repo import ItemEvent
from pipeline.jobs.cubes import JOB_CUBE_APPEND, cube_append_enqueuer, enqueue_cube_append
from pipeline.jobs.cubes import register as register_cube_jobs
from pipeline.queue.memory import InMemoryQueue

pytestmark = pytest.mark.asyncio

T0 = dt.datetime(2026, 10, 3, 17, 2, 36, 714359, tzinfo=dt.UTC)


def _item(item_id, collection="src", **properties):
    properties.setdefault("datetime", "2026-10-03T17:02:36.714359936Z")
    return {
        "id": item_id,
        "collection": collection,
        "properties": properties,
        "assets": {"data": {"href": f"s3://noaa-goes19/{item_id}.nc"}},
    }


def _event(event_id, item_id, op="insert", collection="src"):
    return ItemEvent(
        id=event_id,
        collection_id=collection,
        item_id=item_id,
        op=op,
        occurred_at=dt.datetime(2026, 10, 3, 18, 0, tzinfo=dt.UTC),
    )


async def _noop(*_args):
    return None


class Harness:
    """A dispatch repo + cube repo + in-memory queue with the real cube jobs."""

    def __init__(self, sinks=None):
        self.repo = FakeDispatchRepo()
        self.cubes = FakeCubeRepo(
            sinks=sinks
            if sinks is not None
            else [FakeSink("s1", "src", "cube1"), FakeSink("other", "elsewhere", "cube2")]
        )
        self.queue = InMemoryQueue()
        register_cube_jobs(self.queue, Settings.from_env(env={}), repo=self.cubes)
        self.deliveries: list[list[dict]] = []
        self.finalizes: list[dict] = []

    def add(self, event, item=None):
        self.repo.events.append(event)
        if item is not None:
            self.repo.items[(event.collection_id, event.item_id)] = item

    def deliver_to(self, association_id="d1"):
        # match_item needs a parseable config: path_template is required.
        self.repo.associations["src"] = [
            DeliverAssociation(
                id=association_id, collection_id="src", config={"path_template": "{filename}"}
            )
        ]

    async def _deliver(self, batches):
        self.deliveries.append(batches)

    async def _finalize(self, payloads):
        self.finalizes.extend(payloads)

    async def dispatch(self):
        return await dispatch_once(
            self.repo,
            self._deliver,
            enqueue_finalize=self._finalize,
            mark_delete_gc=_noop,
            cube_repo=self.cubes,
            enqueue_cube_appends=cube_append_enqueuer(self.queue),
        )

    def cube_jobs(self):
        return [j.payload["cube_sink_id"] for j in self.queue.jobs if j.name == JOB_CUBE_APPEND]


async def test_insert_on_a_source_writes_a_pending_row_and_wakes_the_sink():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    result = await h.dispatch()
    [row] = h.cubes.rows("s1")
    assert (row.item_id, row.item_datetime, row.status, row.reason) == ("a", T0, "pending", None)
    assert h.cube_jobs() == ["s1"]
    job = h.queue.jobs[0]
    assert job.lock == job.queueing_lock == "cube:s1"
    assert h.repo.processed == [1]
    assert (result.cube_rows, result.cube_sinks) == (1, 1)


async def test_update_and_delete_events_never_match():
    h = Harness()
    h.add(_event(1, "a", op="update"), _item("a"))
    h.add(_event(2, "b", op="delete"))
    await h.dispatch()
    assert h.cubes.ledger == {}
    assert h.cube_jobs() == []
    assert sorted(h.repo.processed) == [1, 2]


async def test_a_put_delete_plus_insert_writes_one_row():
    h = Harness()
    h.add(_event(1, "a", op="delete"), _item("a"))  # the item is live: replace pair
    h.add(_event(2, "a", op="insert"))
    await h.dispatch()
    # The same PUT replayed later (another delete + insert) adds nothing.
    h.add(_event(3, "a", op="delete"))
    h.add(_event(4, "a", op="insert"))
    await h.dispatch()
    assert [r.item_id for r in h.cubes.rows("s1")] == ["a"]
    assert sorted(h.repo.processed) == [1, 2, 3, 4]


async def test_a_coalesced_enqueue_still_drains_the_event():
    h = Harness()
    await enqueue_cube_append(h.queue, "s1")  # a job is already waiting
    h.add(_event(1, "a"), _item("a"))
    await h.dispatch()
    assert h.cube_jobs() == ["s1"]  # no second job
    assert h.cubes.rows("s1")[0].status == "pending"  # the waiting job will read it
    assert h.repo.processed == [1]


async def test_a_sink_on_another_collection_is_untouched():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    await h.dispatch()
    assert h.cubes.rows("other") == []
    assert "other" not in h.cube_jobs()


async def test_no_datetime_is_skipped_and_wakes_nothing():
    h = Harness()
    item = _item("a")
    item["properties"] = {"datetime": None}
    h.add(_event(1, "a"), item)
    await h.dispatch()
    [row] = h.cubes.rows("s1")
    assert (row.status, row.reason) == ("skipped", REASON_NO_DATETIME)
    assert row.item_datetime == dt.datetime(2026, 10, 3, 18, 0, tzinfo=dt.UTC)  # occurred_at
    assert h.cube_jobs() == []


async def test_start_datetime_is_the_fallback():
    h = Harness()
    item = _item("a", datetime=None, start_datetime="2026-10-03T17:00:00Z")
    h.add(_event(1, "a"), item)
    await h.dispatch()
    assert h.cubes.rows("s1")[0].item_datetime == dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


async def test_an_unparseable_datetime_counts_as_missing():
    h = Harness()
    h.add(_event(1, "a"), _item("a", datetime="yesterday"))
    await h.dispatch()
    assert h.cubes.rows("s1")[0].reason == REASON_NO_DATETIME


async def test_nanosecond_datetime_lands_as_microseconds():
    h = Harness()
    h.add(_event(1, "a"), _item("a", datetime="2026-10-03T17:02:36.714359936Z"))
    await h.dispatch()
    assert h.cubes.rows("s1")[0].item_datetime == T0


async def test_one_job_per_sink_however_many_items():
    h = Harness(sinks=[FakeSink("s1", "src", "cube1"), FakeSink("s2", "src", "cube2")])
    for n in range(50):
        h.add(_event(n + 1, f"i{n}"), _item(f"i{n}"))
    result = await h.dispatch()
    assert sorted(h.cube_jobs()) == ["s1", "s2"]
    assert len(h.cubes.rows("s1")) == len(h.cubes.rows("s2")) == 50
    assert h.cubes.sink_calls == 1  # cached per collection per batch
    assert (result.cube_rows, result.cube_sinks) == (100, 2)


async def test_a_staged_insert_does_not_match():
    h = Harness()
    item = _item("a")
    item["assets"] = {"data": {"href": "staging://up1/scene.tif"}}
    h.add(_event(1, "a"), item)
    await h.dispatch()
    # Decision 13: the staged gate routes it to finalize, whose upsert emits
    # an update, which never feeds a cube. Asserting the finalize proves it
    # took the gate, not the poison-drain.
    assert [p["item_id"] for p in h.finalizes] == ["a"]
    assert h.cubes.ledger == {}


async def test_a_sink_lookup_failure_keeps_the_items_deliveries():
    h = Harness()
    h.cubes.lookup_error = RuntimeError('relation "stac_higher.cube_sinks" does not exist')
    h.deliver_to("d1")
    h.add(_event(1, "a"), _item("a"))
    h.add(_event(2, "b"), _item("b"))
    await h.dispatch()
    assert [b["association_id"] for b in h.deliveries[0]] == ["d1"]
    assert [i["item_id"] for i in h.deliveries[0][0]["items"]] == ["a", "b"]
    assert h.cubes.sink_calls == 1  # the failure is cached for the claim (decision 7)
    assert h.cubes.ledger == {}
    assert h.repo.processed == [1, 2]


async def test_a_ledger_insert_failure_leaves_the_claim_for_a_redrive():
    h = Harness()
    h.cubes.record_error_exc = RuntimeError("db down")
    h.deliver_to("d1")
    h.add(_event(1, "a"), _item("a"))
    with pytest.raises(RuntimeError):
        await h.dispatch()
    assert h.repo.processed == []
    # Decision 6: the cube step runs first. The association matches, so an
    # empty list here means nothing else was queued yet.
    assert h.deliveries == []
    assert h.cube_jobs() == []
    # The redrive (the next wake) delivers once the insert works.
    h.cubes.record_error_exc = None
    h.repo.claimed.clear()
    await h.dispatch()
    assert [b["association_id"] for b in h.deliveries[0]] == ["d1"]
    assert h.cube_jobs() == ["s1"]
    assert h.repo.processed == [1]


async def test_without_a_cube_repo_nothing_changes():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    await dispatch_once(h.repo, h._deliver, enqueue_finalize=_noop, mark_delete_gc=_noop)
    assert h.cubes.ledger == {}
    assert h.repo.processed == [1]


async def test_dispatch_until_empty_passes_the_cube_hooks_through():
    h = Harness()
    h.add(_event(1, "a"), _item("a"))
    await dispatch_until_empty(
        h.repo,
        h._deliver,
        enqueue_finalize=_noop,
        mark_delete_gc=_noop,
        cube_repo=h.cubes,
        enqueue_cube_appends=cube_append_enqueuer(h.queue),
    )
    assert h.cube_jobs() == ["s1"]
