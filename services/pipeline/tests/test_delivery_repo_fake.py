import pytest

from _delivery_fake import FakeDeliveryRepo

pytestmark = pytest.mark.asyncio


async def test_upsert_pending_is_idempotent_per_association_item():
    repo = FakeDeliveryRepo()
    r1 = await repo.upsert_pending("a1", "i1", "2026-01-01T00:00:00+00:00")
    r2 = await repo.upsert_pending("a1", "i1", "2026-01-02T00:00:00+00:00")
    assert r1 == r2  # same (association, item) → same row
    assert repo.rows[r1]["status"] == "pending"
    # a different item gets its own row
    r3 = await repo.upsert_pending("a1", "i2", None)
    assert r3 != r1


async def test_mark_transitions_and_bytes():
    repo = FakeDeliveryRepo()
    rid = await repo.upsert_pending("a1", "i1", None)
    await repo.mark_delivering(rid)
    assert repo.rows[rid]["status"] == "delivering"
    assert repo.rows[rid]["attempts"] == 1
    await repo.mark_delivered(rid, 2048)
    assert repo.rows[rid]["status"] == "delivered"
    assert repo.rows[rid]["bytes"] == 2048


async def test_mark_failed_records_error():
    repo = FakeDeliveryRepo()
    rid = await repo.upsert_pending("a1", "i1", None)
    await repo.mark_failed(rid, "boom")
    assert repo.rows[rid]["status"] == "failed"
    assert repo.rows[rid]["error"] == "boom"
