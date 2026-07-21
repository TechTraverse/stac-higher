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


async def test_matches_and_drains_outbox():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )
    matches = await dispatch_once(repo)
    assert [m.association_id for m in matches] == ["a1"]
    assert repo.processed == [1]


async def test_associations_queried_once_per_collection_in_batch():
    # Two events sharing a collection (e.g. a bulk upsert) must trigger exactly
    # one list_deliver_associations lookup, not one per event.
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=1, collection_id="c", item_id="i1", op="insert"),
            ItemEvent(id=2, collection_id="c", item_id="i2", op="insert"),
        ],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1"), ("c", "i2"): _item("i2")},
    )
    matches = await dispatch_once(repo)
    assert [m.association_id for m in matches] == ["a1", "a1"]
    assert repo.assoc_calls == 1
    assert repo.processed == [1, 2]


async def test_delete_event_is_drained_without_matching():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="gone", op="delete")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
    )
    matches = await dispatch_once(repo)
    assert matches == []
    assert repo.processed == [2]  # deletions never propagate, but the row drains


async def test_missing_item_drains_without_crashing():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},  # item not yet visible (race) — skip, drain, revisit never (best-effort)
    )
    matches = await dispatch_once(repo)
    assert matches == []
    assert repo.processed == [3]
