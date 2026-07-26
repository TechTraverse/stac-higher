import pytest

# NOTE: this test module has no `tests` package (no __init__.py); pytest's
# rootdir-insertion import mode puts `tests/` itself on sys.path, so sibling
# modules import bare (`_dispatch_fake`), matching the established pattern in
# test_ingest_fetch.py — not as `tests._dispatch_fake`.
from _dispatch_fake import FakeDispatchRepo
from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.loop import (
    MAX_VISIBILITY_ATTEMPTS,
    VISIBILITY_RETRY_SECONDS,
    dispatch_once,
    dispatch_until_empty,
)
from pipeline.dispatcher.repo import ItemEvent

pytestmark = pytest.mark.asyncio


def _item(item_id):
    return {"id": item_id, "collection": "c", "properties": {}, "assets": {"data": {}}}


def _collector():
    """Return (enqueue_callable, captured_batches_list)."""
    captured: list[list[dict]] = []

    async def _enqueue(batches):
        captured.append(batches)

    return _enqueue, captured


async def test_matches_and_drains_outbox():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )
    enqueue, captured = _collector()
    matches = (await dispatch_once(repo, enqueue)).matches
    assert [m.association_id for m in matches] == ["a1"]
    assert repo.processed == [1]
    # one batch for association a1 carrying item i1's single asset.
    assert captured == [[{
        "association_id": "a1",
        "items": [{"item_id": "i1", "asset_keys": ["data"], "item_created_at": None}],
    }]]


async def test_associations_queried_once_per_collection_in_batch():
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=1, collection_id="c", item_id="i1", op="insert"),
            ItemEvent(id=2, collection_id="c", item_id="i2", op="insert"),
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1"), ("c", "i2"): _item("i2")},
    )
    enqueue, captured = _collector()
    matches = (await dispatch_once(repo, enqueue)).matches
    assert [m.association_id for m in matches] == ["a1", "a1"]
    assert repo.assoc_calls == 1
    assert repo.processed == [1, 2]
    # both items grouped under ONE association batch (batch-oriented jobs).
    assert captured == [[{
        "association_id": "a1",
        "items": [
            {"item_id": "i1", "asset_keys": ["data"], "item_created_at": None},
            {"item_id": "i2", "asset_keys": ["data"], "item_created_at": None},
        ],
    }]]


async def test_delete_event_is_drained_without_matching():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="gone", op="delete")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
    )
    enqueue, captured = _collector()
    matches = (await dispatch_once(repo, enqueue)).matches
    assert matches == []
    assert repo.processed == [2]  # deletions never propagate, but the row drains
    assert captured == []  # nothing enqueued


async def test_missing_item_is_deferred_for_bounded_retry():
    """I-38: an event whose item is not yet visible is released with a
    cool-off (attempt counted), NOT drained."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    result = await dispatch_once(repo, enqueue)
    assert result.matches == []
    assert result.claimed == 1
    assert repo.processed == []  # NOT drained — retried on a later wake
    assert repo.released == [([3], VISIBILITY_RETRY_SECONDS)]
    assert captured == []
    # The cool-off keeps it out of the next claim (no budget-burning spin).
    assert (await dispatch_once(repo, enqueue)).claimed == 0


async def test_deferred_item_dispatches_once_visible():
    """The deferred event delivers normally when the item appears."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    await dispatch_once(repo, enqueue)
    repo.items[("c", "race")] = _item("race")
    repo.make_due([3])
    result = await dispatch_once(repo, enqueue)
    assert [m.item_id for m in result.matches] == ["race"]
    assert repo.processed == [3]
    assert len(captured) == 1


async def test_missing_item_drains_at_attempt_cap():
    """At MAX_VISIBILITY_ATTEMPTS the event drains loudly (old behavior)."""
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(
                id=3,
                collection_id="c",
                item_id="race",
                op="insert",
                dispatch_attempts=MAX_VISIBILITY_ATTEMPTS,
            )
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    result = await dispatch_once(repo, enqueue)
    assert result.matches == []
    assert repo.processed == [3]
    assert repo.released == []
    assert captured == []


async def test_enqueue_happens_before_mark_processed():
    # If enqueue fails, the outbox rows must NOT be marked processed (at-least-once).
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )

    async def _boom(_batches):
        raise RuntimeError("queue down")

    with pytest.raises(RuntimeError):
        await dispatch_once(repo, _boom)
    assert repo.processed == []  # not drained — a redrive will retry


# --------------------------------------------------------------------------- #
# B-iii: per-event isolation (I-39) + atomic claim (I-40)
# --------------------------------------------------------------------------- #


async def test_poison_event_isolated_and_drained():
    """One event whose item fetch raises must not abort the batch: the healthy
    event still dispatches and BOTH events drain (the poison one dead-letters
    into the logs instead of busy-looping the claim)."""

    class _PoisonRepo(FakeDispatchRepo):
        async def get_item(self, collection_id, item_id):
            if item_id == "poison":
                raise RuntimeError("pgstac exploded")
            return await super().get_item(collection_id, item_id)

    repo = _PoisonRepo(
        events=[
            ItemEvent(id=1, collection_id="c", item_id="poison", op="insert"),
            ItemEvent(id=2, collection_id="c", item_id="ok", op="insert"),
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "ok"): _item("ok")},
    )
    enqueue, captured = _collector()
    matches = (await dispatch_once(repo, enqueue)).matches
    assert [m.item_id for m in matches] == ["ok"]
    assert sorted(repo.processed) == [1, 2]
    assert len(captured) == 1


async def test_overlapping_dispatch_runs_cannot_double_claim():
    """I-40: a second dispatch overlapping the first (claimed, not yet marked)
    must claim nothing — the claim is atomic, not transaction-scoped."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )
    first = await repo.claim_pending_events(10)
    assert [e.id for e in first] == [1]
    # Overlapping run: the row is claimed-but-unprocessed — must not re-claim.
    assert await repo.claim_pending_events(10) == []


# --------------------------------------------------------------------------- #
# Slice C: drain-until-empty (the NOTIFY wake path)
# --------------------------------------------------------------------------- #


async def test_dispatch_until_empty_drains_across_batches():
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=i, collection_id="c", item_id=f"i{i}", op="insert")
            for i in range(1, 4)
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", f"i{i}"): _item(f"i{i}") for i in range(1, 4)},
    )
    enqueue, captured = _collector()
    total = await dispatch_until_empty(repo, enqueue, batch_size=2)
    assert total == 3
    assert sorted(repo.processed) == [1, 2, 3]
    assert len(captured) == 2  # 2-row batch + 1-row batch


async def test_dispatch_until_empty_does_not_spin_on_deferred_events():
    """A wake with only a not-yet-visible item terminates after one release —
    the cool-off keeps the deferred event out of subsequent claims."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, _captured = _collector()
    total = await dispatch_until_empty(repo, enqueue)
    assert total == 0
    assert repo.released == [([1], VISIBILITY_RETRY_SECONDS)]
    assert repo.processed == []
