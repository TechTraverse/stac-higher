"""Finalize sweep (§6.4): TTL expiry + stale-claim requeue."""

from __future__ import annotations

import datetime as dt

from _finalize_fake import FakeFinalizeRepo, Session
from pipeline.finalize.sweep import finalize_sweep_tick

UTC = dt.UTC
TTL = 86400
STALE = 1800


def _old(seconds: int) -> dt.datetime:
    return dt.datetime.now(UTC) - dt.timedelta(seconds=seconds)


async def _collecting_enqueue(payloads_sink):
    async def enqueue(payloads):
        payloads_sink.extend(payloads)

    return enqueue


async def test_pending_rows_past_ttl_expire():
    repo = FakeFinalizeRepo(
        sessions={
            "old": Session(id="old", collection_id="c", created_at=_old(TTL + 60)),
            "fresh": Session(id="fresh", collection_id="c", created_at=_old(60)),
            "done": Session(
                id="done", collection_id="c", status="finalized", created_at=_old(TTL + 60)
            ),
        }
    )
    sent: list[dict] = []

    result = await finalize_sweep_tick(
        repo,
        await _collecting_enqueue(sent),
        ttl_seconds=TTL,
        stale_seconds=STALE,
        batch_limit=100,
    )

    assert result.expired == 1
    assert repo.sessions["old"].status == "expired"
    assert repo.sessions["fresh"].status == "pending"
    assert repo.sessions["done"].status == "finalized"
    assert sent == []


async def test_stale_finalizing_rows_are_requeued_with_null_op():
    repo = FakeFinalizeRepo(
        sessions={
            "stale": Session(
                id="stale",
                collection_id="c",
                status="finalizing",
                item_id="item-1",
                claimed_at=_old(STALE + 60),
            ),
            "active": Session(
                id="active",
                collection_id="c",
                status="finalizing",
                item_id="item-2",
                claimed_at=_old(60),
            ),
        }
    )
    sent: list[dict] = []

    result = await finalize_sweep_tick(
        repo,
        await _collecting_enqueue(sent),
        ttl_seconds=TTL,
        stale_seconds=STALE,
        batch_limit=100,
    )

    assert result.requeued == 1
    # flipped back to pending FIRST, then re-enqueued with no recorded op —
    # the recorder's conservative branch handles a rejection on the re-run
    assert repo.sessions["stale"].status == "pending"
    assert repo.sessions["active"].status == "finalizing"
    assert sent == [
        {"upload_id": "stale", "collection_id": "c", "item_id": "item-1", "event_op": None}
    ]


async def test_stale_claim_without_bound_item_is_left_to_ttl():
    repo = FakeFinalizeRepo(
        sessions={
            "odd": Session(
                id="odd",
                collection_id="c",
                status="finalizing",
                item_id=None,
                claimed_at=_old(STALE + 60),
            ),
        }
    )
    sent: list[dict] = []

    result = await finalize_sweep_tick(
        repo,
        await _collecting_enqueue(sent),
        ttl_seconds=TTL,
        stale_seconds=STALE,
        batch_limit=100,
    )

    assert result.requeued == 0
    assert sent == []
    # still released so the TTL expiry can eventually take it
    assert repo.sessions["odd"].status == "pending"
