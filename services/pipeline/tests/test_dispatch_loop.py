import pytest

# NOTE: this test module has no `tests` package (no __init__.py); pytest's
# rootdir-insertion import mode puts `tests/` itself on sys.path, so sibling
# modules import bare (`_dispatch_fake`), matching the established pattern in
# test_ingest_fetch.py — not as `tests._dispatch_fake`.
from _dispatch_fake import FakeDispatchRepo
from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.loop import dispatch_once
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
    matches = await dispatch_once(repo, enqueue)
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
    matches = await dispatch_once(repo, enqueue)
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
    matches = await dispatch_once(repo, enqueue)
    assert matches == []
    assert repo.processed == [2]  # deletions never propagate, but the row drains
    assert captured == []  # nothing enqueued


async def test_missing_item_drains_without_crashing():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    matches = await dispatch_once(repo, enqueue)
    assert matches == []
    assert repo.processed == [3]
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
    matches = await dispatch_once(repo, enqueue)
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
