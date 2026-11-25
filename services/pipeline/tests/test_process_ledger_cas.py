"""K-4 compare-and-set ledger (plan Invariants I1-I10) against the fake.

The fake is what every later unit test leans on, so its predicates are pinned
here; the Pg statements are pinned by tests/test_integration_process_reconcile.py.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from _process_fake import FakeProcessRepo

NOW = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.UTC)
EARLIER = NOW - dt.timedelta(minutes=5)


async def _claimed(repo: FakeProcessRepo, **row):
    run_id = await repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id=row.pop("source_id", None),
        input_items=[{"item_id": "a"}], deferred_until=None,
    )
    record = repo._row(run_id)
    record.update(row)
    run = await repo.claim_run(run_id, NOW)
    assert run is not None
    return run


async def _submitted(repo: FakeProcessRepo, handle: str = "h1", **row):
    run = await _claimed(repo, **row)
    record = await repo.record_submission(
        run.id, claimed_at=run.claimed_at, handle=handle, backend="docker",
        phase="running", submitted_at=NOW, credentials_expire_at=None,
    )
    assert record is not None
    return run


async def test_a_claim_returns_its_stamp_and_clears_the_executor_columns():
    repo = FakeProcessRepo()
    run = await _claimed(
        repo, executor_handle="old", submitted_at=EARLIER, credentials_expire_at=EARLIER
    )
    row = repo._row(run.id)
    assert run.claimed_at == NOW and row["started_at"] == NOW
    assert row["executor_handle"] is None
    assert row["submitted_at"] is None and row["credentials_expire_at"] is None
    assert row["executor_backend"] == "docker"  # K-3's admitted marker stays stamped


async def test_finish_with_a_stale_claim_stamp_matches_nothing():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    assert await repo.finish_run(
        run.id, status="dead", error="x", log_ref=None, next_attempt_at=None,
        claimed_at=EARLIER,
    ) is None
    assert repo._row(run.id)["status"] == "running"


async def test_finish_lands_cancelled_when_a_cancel_was_requested():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    repo._row(run.id)["cancel_requested_at"] = NOW
    written = await repo.finish_run(
        run.id, status="dead", error="boom", log_ref=None, next_attempt_at=None,
        claimed_at=run.claimed_at,
    )
    assert written == "cancelled"
    assert repo._row(run.id)["error"] == "cancelled by an operator; boom"


async def test_finish_refuses_a_claimed_queued_status():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    with pytest.raises(ValueError):
        await repo.finish_run(
            run.id, status="queued", error=None, log_ref=None, next_attempt_at=None,
            claimed_at=run.claimed_at,
        )


async def test_requeue_without_attempt_restores_the_attempt_and_uses_next_attempt_at():
    repo = FakeProcessRepo()
    run = await _claimed(repo, attempts=1)  # the claim makes it 2
    retry = NOW + dt.timedelta(seconds=60)
    outcome = await repo.requeue_without_attempt(
        run.id, claimed_at=run.claimed_at, error="daemon down", next_attempt_at=retry
    )
    row = repo._row(run.id)
    assert outcome == "requeued"
    assert row["status"] == "queued" and row["attempts"] == 1
    assert row["next_attempt_at"] == retry and row.get("finished_at") is None
    assert row["phase"] is None and row["executor_backend"] is None


async def test_requeue_with_cancel_requested_lands_cancelled():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    repo._row(run.id)["cancel_requested_at"] = NOW
    outcome = await repo.requeue_without_attempt(
        run.id, claimed_at=run.claimed_at, error="daemon down", next_attempt_at=NOW
    )
    assert outcome == "cancelled" and repo._row(run.id)["status"] == "cancelled"


async def test_record_submission_is_fenced_and_one_shot():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    first = await repo.record_submission(
        run.id, claimed_at=run.claimed_at, handle="h1", backend="docker", phase="running",
        submitted_at=NOW, credentials_expire_at=None,
    )
    second = await repo.record_submission(
        run.id, claimed_at=run.claimed_at, handle="h2", backend="docker", phase="running",
        submitted_at=NOW, credentials_expire_at=None,
    )
    assert first is not None and first.cancel_requested is False
    assert second is None
    assert repo._row(run.id)["executor_handle"] == "h1"


async def test_record_submission_reports_an_earlier_cancel_request():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    repo._row(run.id)["cancel_requested_at"] = NOW
    record = await repo.record_submission(
        run.id, claimed_at=run.claimed_at, handle="h1", backend="docker", phase="running",
        submitted_at=NOW, credentials_expire_at=None,
    )
    assert record is not None and record.cancel_requested is True


async def test_record_submission_with_a_stale_stamp_matches_nothing():
    repo = FakeProcessRepo()
    run = await _claimed(repo)
    assert await repo.record_submission(
        run.id, claimed_at=EARLIER, handle="h1", backend="docker", phase="running",
        submitted_at=NOW, credentials_expire_at=None,
    ) is None


async def test_set_phase_needs_the_current_handle():
    repo = FakeProcessRepo()
    run = await _submitted(repo)
    assert await repo.set_phase(run.id, handle="other", phase="running", detail=None) is False
    assert await repo.set_phase(run.id, handle="h1", phase="pending_capacity", detail="q")
    assert await repo.set_phase(run.id, handle="h1", phase="pending_capacity", detail="q") is False


async def test_collecting_yields_none_for_a_row_already_collected():
    repo = FakeProcessRepo()
    run = await _submitted(repo)
    async with repo.collecting(run.id, handle="h1") as collecting:
        assert collecting is not None
        await collecting.finish(status="succeeded", error=None, log_ref="k", 
                                next_attempt_at=None, at=NOW)
    async with repo.collecting(run.id, handle="h1") as again:
        assert again is None


async def test_collecting_finish_moves_the_row_out_of_running_once():
    repo = FakeProcessRepo()
    run = await _submitted(repo, source_id="s1")
    async with repo.collecting(run.id, handle="h1") as collecting:
        assert await collecting.finish(
            status="succeeded", error=None, log_ref="k", next_attempt_at=None, at=NOW
        ) == "succeeded"
        with pytest.raises(RuntimeError):
            await collecting.finish(
                status="succeeded", error=None, log_ref="k", next_attempt_at=None, at=NOW
            )
    assert repo._row(run.id)["status"] == "succeeded"
    assert repo.source_runs == [("s1", True)]


async def test_two_concurrent_collectors_get_one_row():
    repo = FakeProcessRepo()
    run = await _submitted(repo)
    seen: list[object] = []
    gate = asyncio.Event()

    async def collect():
        async with repo.collecting(run.id, handle="h1") as collecting:
            seen.append(collecting)
            await gate.wait()
            if collecting is not None:
                await collecting.finish(status="succeeded", error=None, log_ref=None,
                                        next_attempt_at=None, at=NOW)

    first = asyncio.create_task(collect())
    second = asyncio.create_task(collect())
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)
    assert sum(c is not None for c in seen) == 1


async def test_a_collector_that_raises_rolls_back():
    repo = FakeProcessRepo()
    run = await _submitted(repo, source_id="s1")
    with pytest.raises(OSError):
        async with repo.collecting(run.id, handle="h1") as collecting:
            await collecting.finish(status="succeeded", error=None, log_ref="k",
                                    next_attempt_at=None, at=NOW)
            raise OSError("crash before commit")
    assert repo._row(run.id)["status"] == "running"
    assert repo.source_runs == []
    async with repo.collecting(run.id, handle="h1") as collecting:
        assert collecting is not None


async def test_a_collector_requeue_folds_into_an_occupied_slot():
    repo = FakeProcessRepo()
    run = await _submitted(repo, source_id="s1")
    holder = await repo.enqueue_run(
        process_id="p1", revision_id="rev-1", source_id="s1",
        input_items=[{"item_id": "b"}], deferred_until=None,
    )
    async with repo.collecting(run.id, handle="h1") as collecting:
        outcome = await collecting.requeue(error="lost", phase=None, phase_detail=None,
                                           next_attempt_at=NOW)
    assert outcome == "merged"
    assert repo._row(run.id) is None
    assert repo._row(holder)["input_items"] == [{"item_id": "b"}, {"item_id": "a"}]


async def test_stall_sweep_skips_submitted_rows():
    repo = FakeProcessRepo()
    stale = await _claimed(repo)
    submitted = await _submitted(repo, handle="h2")
    for run in (stale, submitted):
        repo._row(run.id)["started_at"] = NOW - dt.timedelta(hours=2)
    assert await repo.reset_stalled_runs(NOW - dt.timedelta(hours=1), 10) == 1
    assert repo._row(stale.id)["status"] == "queued"
    assert repo._row(submitted.id)["status"] == "running"
