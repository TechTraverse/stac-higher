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


def _staged_item(item_id, upload_id="up1", filename="scene.tif"):
    """An item as a push client PUT it: staged asset hrefs (spec §4.2)."""
    return {
        "id": item_id,
        "collection": "c",
        "properties": {},
        "assets": {"data": {"href": f"staging://{upload_id}/{filename}"}},
    }


def _finalized_item(item_id, filename="scene.tif"):
    """The same item after finalize's upsert: canonical /api/assets hrefs."""
    return {
        "id": item_id,
        "collection": "c",
        "properties": {},
        "assets": {"data": {"href": f"/api/assets/c/{item_id}/{filename}"}},
    }


def _collector():
    """Return (enqueue_callable, captured_batches_list)."""
    captured: list[list[dict]] = []

    async def _enqueue(batches):
        captured.append(batches)

    return _enqueue, captured


def _finalize_collector():
    """Return (enqueue_finalize_callable, captured_payload_lists)."""
    captured: list[list[dict]] = []

    async def _enqueue(payloads):
        captured.append(payloads)

    return _enqueue, captured


def _mark_collector():
    """Return (mark_delete_gc_callable, marked_(collection,item)_list)."""
    marked: list[tuple[str, str]] = []

    async def _mark(collection_id, item_id):
        marked.append((collection_id, item_id))

    return _mark, marked


def _deps():
    """Default staged-gate/GC collaborators for tests not exercising them."""
    enqueue_finalize, _finalized = _finalize_collector()
    mark, _marked = _mark_collector()
    return {"enqueue_finalize": enqueue_finalize, "mark_delete_gc": mark}


async def test_matches_and_drains_outbox():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )
    enqueue, captured = _collector()
    matches = (await dispatch_once(repo, enqueue, **_deps())).matches
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
    matches = (await dispatch_once(repo, enqueue, **_deps())).matches
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


async def test_missing_item_is_deferred_for_bounded_retry():
    """I-38: an event whose item is not yet visible is released with a
    cool-off (attempt counted), NOT drained."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    result = await dispatch_once(repo, enqueue, **_deps())
    assert result.matches == []
    assert result.claimed == 1
    assert repo.processed == []  # NOT drained — retried on a later wake
    assert repo.released == [([3], VISIBILITY_RETRY_SECONDS)]
    assert captured == []
    # The cool-off keeps it out of the next claim (no budget-burning spin).
    assert (await dispatch_once(repo, enqueue, **_deps())).claimed == 0


async def test_deferred_item_dispatches_once_visible():
    """The deferred event delivers normally when the item appears."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},
    )
    enqueue, captured = _collector()
    await dispatch_once(repo, enqueue, **_deps())
    repo.items[("c", "race")] = _item("race")
    repo.make_due([3])
    result = await dispatch_once(repo, enqueue, **_deps())
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
    result = await dispatch_once(repo, enqueue, **_deps())
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
        await dispatch_once(repo, _boom, **_deps())
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
    matches = (await dispatch_once(repo, enqueue, **_deps())).matches
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
    total = await dispatch_until_empty(repo, enqueue, **_deps(), batch_size=2)
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
    total = await dispatch_until_empty(repo, enqueue, **_deps())
    assert total == 0
    assert repo.released == [([1], VISIBILITY_RETRY_SECONDS)]
    assert repo.processed == []


# --------------------------------------------------------------------------- #
# P7-F §7.1/§7.2: the staged gate
# --------------------------------------------------------------------------- #


async def test_staged_insert_routes_to_finalize_not_delivery():
    """§7.1: an insert whose item carries staging:// hrefs enqueues a
    pipeline.finalize payload (P7-E contract), drains, and never matches
    delivery associations."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _staged_item("i1", upload_id="up1")},
    )
    enqueue, captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, marked = _mark_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert finalized == [[{
        "upload_id": "up1",
        "collection_id": "c",
        "item_id": "i1",
        "event_op": "insert",
    }]]
    assert result.matches == []
    assert result.finalizes == 1
    assert repo.processed == [1]  # drained — finalize now owns the item
    assert captured == []  # no delivery batch
    assert repo.assoc_calls == 0  # associations never even consulted
    assert marked == []


async def test_staged_update_event_carries_update_op():
    """A second client PUT while still staged re-enqueues finalize with
    event_op=update (the ledger claim downstream makes duplicates no-ops)."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="update")],
        items={("c", "i1"): _staged_item("i1", upload_id="up2")},
    )
    enqueue, _captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, _marked = _mark_collector()
    await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert finalized == [[{
        "upload_id": "up2",
        "collection_id": "c",
        "item_id": "i1",
        "event_op": "update",
    }]]
    assert repo.processed == [1]


async def test_upload_id_parsed_from_first_staged_href():
    """A mixed-asset item (canonical + staged) is still gated, and the
    upload_id comes from the FIRST staged href in asset order."""
    item = {
        "id": "i1",
        "collection": "c",
        "properties": {},
        "assets": {
            "thumb": {"href": "/api/assets/c/i1/thumb.png"},
            "data": {"href": "staging://up9/scene.tif"},
            "meta": {"href": "staging://up9/scene.json"},
        },
    }
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        items={("c", "i1"): item},
    )
    enqueue, _captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, _marked = _mark_collector()
    await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert finalized[0][0]["upload_id"] == "up9"


async def test_finalized_update_event_dispatches_to_delivery_once():
    """§7.2 no-double-fire / no-no-fire pair: finalize's upsert emits an
    update event whose hrefs are canonical — by claim time the gate sees no
    staged hrefs and the event flows to delivery exactly as before."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="i1", op="update")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _finalized_item("i1")},
    )
    enqueue, captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, _marked = _mark_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert finalized == []  # not re-gated
    assert [m.association_id for m in result.matches] == ["a1"]
    assert len(captured) == 1  # delivered ONCE
    assert repo.processed == [2]


async def test_finalize_enqueue_happens_before_drain():
    """The P7-E contract: enqueue BEFORE draining the staged event. A failed
    finalize enqueue must leave the outbox row unprocessed for a redrive."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        items={("c", "i1"): _staged_item("i1")},
    )
    enqueue, _captured = _collector()

    async def _boom(_payloads):
        raise RuntimeError("queue down")

    mark, _marked = _mark_collector()
    with pytest.raises(RuntimeError):
        await dispatch_once(repo, enqueue, enqueue_finalize=_boom, mark_delete_gc=mark)
    assert repo.processed == []


async def test_mixed_batch_splits_staged_and_canonical():
    """One claim with a staged and a canonical item: one finalize payload,
    one delivery batch, both events drained."""
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=1, collection_id="c", item_id="staged", op="insert"),
            ItemEvent(id=2, collection_id="c", item_id="ready", op="insert"),
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={
            ("c", "staged"): _staged_item("staged", upload_id="up3"),
            ("c", "ready"): _item("ready"),
        },
    )
    enqueue, captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, _marked = _mark_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert [p["item_id"] for batch in finalized for p in batch] == ["staged"]
    assert [m.item_id for m in result.matches] == ["ready"]
    assert len(captured) == 1
    assert sorted(repo.processed) == [1, 2]


async def test_malformed_staged_href_dead_letters():
    """A staged href a mint could never have produced (no filename) cannot be
    fixed by retrying: strict parse raises and the event takes the I-39
    poison-drain — logged, drained, nothing enqueued anywhere."""
    item = {
        "id": "i1",
        "collection": "c",
        "properties": {},
        "assets": {"data": {"href": "staging://../evil"}},
    }
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): item},
    )
    enqueue, captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, _marked = _mark_collector()
    await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert finalized == []
    assert captured == []
    assert repo.processed == [1]
    assert repo.released == []


# --------------------------------------------------------------------------- #
# P7-F §7.3: delete events mark asset_gc before draining
# --------------------------------------------------------------------------- #


async def test_delete_event_marks_gc_and_drains_without_matching():
    """§7.3: a delete event marks the item's canonical prefix (via the GC
    callback) BEFORE draining, and still never matches delivery associations."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="gone", op="delete")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
    )
    enqueue, captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, marked = _mark_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert marked == [("c", "gone")]
    assert result.matches == []
    assert repo.processed == [2]  # deletions never propagate, but the row drains
    assert captured == []
    assert finalized == []
    assert repo.assoc_calls == 0


async def test_delete_event_for_live_item_skips_mark():
    """I-46: the transaction-API path splits a client PUT into delete+insert
    events. The delete leg of that pair must NOT mark asset_gc — the item is
    alive and marking would schedule its canonical bytes for collection. If
    the item exists at claim time, skip the mark and drain."""
    live = {"id": "alive", "collection": "c", "assets": {}}
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=7, collection_id="c", item_id="alive", op="delete")],
        items={("c", "alive"): live},
    )
    enqueue, captured = _collector()
    enqueue_finalize, finalized = _finalize_collector()
    mark, marked = _mark_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=mark
    )
    assert marked == []  # no mark for a live item
    assert repo.processed == [7]  # drained, not deferred
    assert result.matches == []
    assert captured == []
    assert finalized == []


async def test_delete_gc_mark_failure_defers_not_drains():
    """A transient mark failure must NOT take the poison-drain (that would
    drain the event with no mark written — the I-51 orphan shape): the event
    is released with the I-38 cool-off and a later tick retries the mark."""
    attempts: list[str] = []

    async def _flaky_mark(collection_id, item_id):
        attempts.append(item_id)
        if len(attempts) == 1:
            raise RuntimeError("db blip")

    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="gone", op="delete")],
    )
    enqueue, _captured = _collector()
    enqueue_finalize, _finalized = _finalize_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=_flaky_mark
    )
    assert result.claimed == 1
    assert repo.processed == []  # undrained — the mark never committed
    assert repo.released == [([2], VISIBILITY_RETRY_SECONDS)]
    # The cool-off keeps it out of the next claim window…
    assert (await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=_flaky_mark
    )).claimed == 0
    # …and a later tick (cool-off passed, mark healthy) marks + drains it.
    repo.make_due([2])
    await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=_flaky_mark
    )
    assert attempts == ["gone", "gone"]
    assert repo.processed == [2]


async def test_delete_gc_mark_failure_drains_at_attempt_cap():
    """At the bounded-retry cap the delete event drains with a loud log —
    the outbox must not wedge on a permanently failing mark."""

    async def _always_fails(collection_id, item_id):
        raise RuntimeError("db down for good")

    repo = FakeDispatchRepo(
        events=[
            ItemEvent(
                id=2,
                collection_id="c",
                item_id="gone",
                op="delete",
                dispatch_attempts=MAX_VISIBILITY_ATTEMPTS,
            )
        ],
    )
    enqueue, _captured = _collector()
    enqueue_finalize, _finalized = _finalize_collector()
    result = await dispatch_once(
        repo, enqueue, enqueue_finalize=enqueue_finalize, mark_delete_gc=_always_fails
    )
    assert result.claimed == 1
    assert repo.processed == [2]
    assert repo.released == []
