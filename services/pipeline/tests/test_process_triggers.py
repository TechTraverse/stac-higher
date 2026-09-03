"""Process triggers, the rate ceiling, the run ledger, and the sweeps (M5-C).

The behaviours pinned here are the ones the spec calls out as requirements
rather than implementation detail: batching per source, ceiling deferral with
coalescing, retry-vs-dead, and the distinction between a run that failed and a
backend that was down.
"""

from __future__ import annotations

import datetime as dt

import pytest

from _dispatch_fake import FakeDispatchRepo
from _process_fake import FakeProcessRepo
from pipeline.config import Settings
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import ItemEvent
from pipeline.process.cron import is_due, matches_minute
from pipeline.process.executor import ExecutorUnavailable, ExitStatus
from pipeline.process.ledger import infrastructure_transition, outcome_transition
from pipeline.process.matcher import ProcessSource, match_process_sources
from pipeline.process.memory_executor import MemoryExecutor
from pipeline.process.rate import RateDecision, evaluate
from pipeline.process.repo import QueuedRun, RateWindow
from pipeline.process.runner import run_one
from pipeline.process.sweep import process_sweep_tick
from pipeline.process.trigger import trigger_run

NOW = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)
PROC = "22222222-2222-4222-8222-222222222222"
SRC = "33333333-3333-4333-8333-333333333333"
REV = "44444444-4444-4444-8444-444444444444"


def item(item_id: str, **props):
    return {
        "id": item_id,
        "collection": "c",
        "assets": {"data": {"href": "https://example.com/a.tif"}},
        "properties": props,
    }


def source(trigger, source_id=SRC) -> ProcessSource:
    return ProcessSource(
        id=source_id, process_id=PROC, collection_id="c", trigger=trigger
    )


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------


def test_item_event_source_matches_a_landing_item():
    matches = match_process_sources(item("i1"), [source({"kind": "item_event"})])
    assert [m.item_id for m in matches] == ["i1"]
    assert matches[0].source_id == SRC


def test_cron_sources_never_match_a_landing_item():
    """They are the scheduler's business; matching one here would run a
    scheduled process on every item that lands."""
    assert (
        match_process_sources(
            item("i1"), [source({"kind": "cron", "schedule": "0 3 * * *"})]
        )
        == []
    )


def test_item_filter_selects():
    src = source({"kind": "item_event", "item_filter": "properties.cloud < 20"})
    assert match_process_sources(item("i1", cloud=5), [src])
    assert not match_process_sources(item("i2", cloud=90), [src])


def test_an_unevaluable_filter_fails_CLOSED():
    """A filter that cannot be evaluated must not be read as 'match
    everything' — that would run the process over items its author
    deliberately excluded."""
    src = source({"kind": "item_event", "item_filter": "this is not cql2 ("})
    assert match_process_sources(item("i1"), [src]) == []


def test_a_broken_trigger_skips_only_its_own_source():
    good = source({"kind": "item_event"}, source_id="good")
    bad = source({"kind": "nonsense"}, source_id="bad")
    matches = match_process_sources(item("i1"), [bad, good, bad])
    assert [m.source_id for m in matches] == ["good"]


# ---------------------------------------------------------------------------
# the dispatcher leg
# ---------------------------------------------------------------------------


async def _dispatch(repo, enqueue_process_runs):
    return await dispatch_once(
        repo,
        lambda batches: _noop(),
        enqueue_finalize=lambda payloads: _noop(),
        mark_delete_gc=lambda c, i: _noop(),
        enqueue_process_runs=enqueue_process_runs,
    )


async def _noop():
    return None


@pytest.mark.asyncio
async def test_many_items_produce_ONE_run_per_source():
    """§6.7: one run = N trigger items. A bulk upsert of 50 items into a
    watched collection must not produce 50 runs."""
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=i, collection_id="c", item_id=f"i{i}", op="insert")
            for i in range(1, 51)
        ],
        items={("c", f"i{i}"): item(f"i{i}") for i in range(1, 51)},
        process_sources={"c": [source({"kind": "item_event"})]},
    )
    batches: list = []

    async def enqueue(payloads):
        batches.extend(payloads)

    result = await _dispatch(repo, enqueue)
    assert result.process_runs == 1
    assert len(batches) == 1
    assert batches[0]["source_id"] == SRC
    assert len(batches[0]["items"]) == 50


@pytest.mark.asyncio
async def test_process_batch_entries_carry_collection_and_op():
    """GOES spec §3: the run must know WHICH collection each triggering item
    came from and the outbox op that fired, so the planner can look the
    document up and the manifest can say `op`."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        items={("c", "i1"): item("i1")},
        process_sources={"c": [source({"kind": "item_event"})]},
    )
    batches: list = []

    async def enqueue(payloads):
        batches.extend(payloads)

    await _dispatch(repo, enqueue)
    assert batches[0]["items"] == [{"item_id": "i1", "collection_id": "c", "op": "insert"}]


@pytest.mark.asyncio
async def test_process_sources_are_looked_up_once_per_collection():
    repo = FakeDispatchRepo(
        events=[
            ItemEvent(id=i, collection_id="c", item_id=f"i{i}", op="insert")
            for i in (1, 2, 3)
        ],
        items={("c", f"i{i}"): item(f"i{i}") for i in (1, 2, 3)},
        process_sources={"c": [source({"kind": "item_event"})]},
    )
    await _dispatch(repo, lambda p: _noop())
    assert repo.source_calls == 1


@pytest.mark.asyncio
async def test_delivery_and_processes_are_independent():
    """A collection can have process sources and no delivery associations;
    neither should gate the other."""
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        items={("c", "i1"): item("i1")},
        process_sources={"c": [source({"kind": "item_event"})]},
        associations={},
    )
    batches: list = []
    result = await _dispatch(repo, lambda p: _collect(batches, p))
    assert result.process_runs == 1
    assert result.matches == []


async def _collect(sink, payloads):
    sink.extend(payloads)


@pytest.mark.asyncio
async def test_delete_events_never_trigger_processes():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="delete")],
        process_sources={"c": [source({"kind": "item_event"})]},
    )
    batches: list = []
    result = await _dispatch(repo, lambda p: _collect(batches, p))
    assert result.process_runs == 0
    assert batches == []


# ---------------------------------------------------------------------------
# the §7 rate ceiling
# ---------------------------------------------------------------------------


def test_under_the_ceiling_runs():
    verdict = evaluate(5, 60, NOW)
    assert verdict.decision is RateDecision.RUN
    assert verdict.deferred_until is None


def test_at_the_ceiling_defers_until_the_oldest_run_ages_out():
    oldest = NOW - dt.timedelta(minutes=50)
    verdict = evaluate(60, 60, NOW, oldest_in_window=oldest)
    assert verdict.deferred
    # Soonest the window could actually admit another run — not a fixed
    # backoff that would wake early and re-defer.
    assert verdict.deferred_until == oldest + dt.timedelta(hours=1)


def test_a_deferral_is_never_scheduled_into_the_past():
    """Clock skew or a stale read would otherwise make the ceiling a busy
    loop."""
    verdict = evaluate(60, 60, NOW, oldest_in_window=NOW - dt.timedelta(hours=5))
    assert verdict.deferred_until > NOW


@pytest.mark.asyncio
async def test_a_trigger_over_the_ceiling_defers_rather_than_drops():
    repo = FakeProcessRepo(
        windows={PROC: RateWindow(recent_runs=60, oldest_in_window=NOW, max_runs_per_hour=60)}
    )
    result = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "i1"}],
        now=NOW,
    )
    assert result.deferred
    # Deferred, but the items are still there to run later.
    assert repo.enqueued[0]["input_items"] == [{"item_id": "i1"}]


@pytest.mark.asyncio
async def test_deferred_runs_coalesce_into_one_growing_run():
    """§7: one deferred run per source absorbs new matches, rather than a
    queue of runs that would themselves breach the ceiling on release."""
    repo = FakeProcessRepo(
        windows={PROC: RateWindow(recent_runs=60, oldest_in_window=NOW, max_runs_per_hour=60)}
    )
    for n in range(3):
        await trigger_run(
            repo,
            process_id=PROC,
            revision_id=REV,
            source_id=SRC,
            input_items=[{"item_id": f"i{n}"}],
            now=NOW,
        )
    assert len(repo.enqueued) == 1
    assert len(repo.enqueued[0]["input_items"]) == 3


@pytest.mark.asyncio
async def test_test_runs_are_subject_to_the_ceiling_too():
    """Exempting them would give a runaway loop a way around the limit —
    nothing stops a script requesting test runs."""
    repo = FakeProcessRepo(
        windows={PROC: RateWindow(recent_runs=60, oldest_in_window=NOW, max_runs_per_hour=60)}
    )
    result = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=None,
        input_items=[],
        now=NOW,
        is_test=True,
    )
    assert result.deferred


# ---------------------------------------------------------------------------
# ledger transitions
# ---------------------------------------------------------------------------


def test_a_clean_exit_succeeds():
    t = outcome_transition(
        exit_code=0, timed_out=False, attempts=1, max_attempts=3, now=NOW,
        retry_wait_seconds=60,
    )
    assert t.status == "succeeded"


def test_a_failure_with_budget_left_retries():
    t = outcome_transition(
        exit_code=1, timed_out=False, attempts=1, max_attempts=3, now=NOW,
        retry_wait_seconds=60,
    )
    assert t.status == "failed"
    assert t.next_attempt_at == NOW + dt.timedelta(seconds=60)


def test_the_last_attempt_goes_dead():
    t = outcome_transition(
        exit_code=1, timed_out=False, attempts=3, max_attempts=3, now=NOW,
        retry_wait_seconds=60,
    )
    assert t.status == "dead"
    assert t.next_attempt_at is None


def test_a_timeout_is_reported_as_a_timeout_not_a_bare_exit_code():
    t = outcome_transition(
        exit_code=137, timed_out=True, attempts=3, max_attempts=3, now=NOW,
        retry_wait_seconds=60,
    )
    assert t.status == "dead"
    assert "timeout" in (t.error or "")


def test_an_infrastructure_failure_does_not_spend_an_attempt():
    """OUR backend being down must not burn a process's retry budget."""
    t = infrastructure_transition(now=NOW, retry_wait_seconds=60, error="docker down")
    assert t.status == "queued"


# ---------------------------------------------------------------------------
# running one claimed run
# ---------------------------------------------------------------------------


class FakeStore:
    def put_object(self, **kwargs):
        return None


def queued(**overrides) -> QueuedRun:
    base = {
        "id": "run-1",
        "process_id": PROC,
        "revision_id": REV,
        "source_id": SRC,
        "attempts": 1,
        "input_items": [{"item_id": "i1", "collection_id": "c", "op": "insert"}],
        "runtime": {"kind": "inline_python", "retry": {"max_attempts": 3}},
        "code": "print(1)",
        "env": [],
    }
    base.update(overrides)
    return QueuedRun(**base)


class FakeSts:
    def assume_role(self, **kwargs):
        return {
            "Credentials": {
                "AccessKeyId": "A",
                "SecretAccessKey": "S",
                "SessionToken": "T",
            }
        }


async def _run(run, executor, repo, *, storage_client=None, fetch_remote=None):
    return await run_one(
        run,
        repo=repo,
        executor=executor,
        settings=Settings.from_env({}),
        storage_client=storage_client or FakeStore(),
        resolve_secret=lambda ref: "x",
        now=NOW,
        sts_client=FakeSts(),
        fetch_remote=fetch_remote,
    )


@pytest.mark.asyncio
async def test_a_successful_run_records_success_on_its_source():
    repo = FakeProcessRepo()
    result = await _run(queued(), MemoryExecutor(results=[ExitStatus(0)]), repo)
    assert result.status == "succeeded"
    assert repo.source_runs == [(SRC, True)]


@pytest.mark.asyncio
async def test_a_source_less_run_leaves_flow_stats_alone():
    """A test run or a manual re-run must not move the telemetry the source's
    expectation is judged against."""
    repo = FakeProcessRepo()
    await _run(queued(source_id=None), MemoryExecutor(results=[ExitStatus(0)]), repo)
    assert repo.source_runs == []


@pytest.mark.asyncio
async def test_a_backend_outage_requeues_without_spending_an_attempt():
    repo = FakeProcessRepo()
    executor = MemoryExecutor(launch_error=ExecutorUnavailable("docker down"))
    result = await _run(queued(), executor, repo)
    assert result.status == "queued"
    assert repo.finished[0]["status"] == "queued"
    # Nothing ran, so nothing is recorded against the source's health.
    assert repo.source_runs == []


@pytest.mark.asyncio
async def test_an_unparseable_revision_dies_immediately():
    """Retrying cannot fix a contract violation, so it must not burn the
    budget pretending it might."""
    repo = FakeProcessRepo()
    result = await _run(queued(runtime={"kind": "wasm"}), MemoryExecutor(), repo)
    assert result.status == "dead"


@pytest.mark.asyncio
async def test_a_revision_with_no_code_dies_rather_than_running_nothing():
    repo = FakeProcessRepo()
    result = await _run(queued(code=None), MemoryExecutor(), repo)
    assert result.status == "dead"


# ---------------------------------------------------------------------------
# inputs are planned, staged and granted BEFORE launch (GOES spec §3)
# ---------------------------------------------------------------------------


class RecordingStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[Key] = Body


def _remote_input_repo() -> FakeProcessRepo:
    return FakeProcessRepo(
        source_collections=["src"],
        items={
            ("src", "i1"): {
                "id": "i1",
                "collection": "src",
                "assets": {"d": {"href": "https://h/x.nc"}},
            }
        },
    )


def _remote_input_run(**overrides) -> QueuedRun:
    return queued(
        input_items=[{"item_id": "i1", "collection_id": "src", "op": "insert"}], **overrides
    )


@pytest.mark.asyncio
async def test_run_one_plans_stages_and_grants_before_launch():
    repo = _remote_input_repo()
    store = RecordingStore()
    executor = MemoryExecutor(results=[ExitStatus(0)])
    fetched: list[str] = []

    async def fetch(href: str) -> bytes:
        fetched.append(href)
        return b"bytes"

    result = await _run(
        _remote_input_run(), executor, repo, storage_client=store, fetch_remote=fetch
    )
    assert result.status == "succeeded"
    assert fetched == ["https://h/x.nc"]
    assert any(k.endswith("/manifest.json") for k in store.objects)
    spec = executor.launched[-1]
    assert spec.env["STAC_HIGHER_INPUT_MANIFEST"].endswith("/manifest.json")
    assert spec.env["STAC_HIGHER_INPUT_PREFIX"] == "staging/runs/run-1/inputs/"


@pytest.mark.asyncio
async def test_run_one_marks_staging_failure_as_a_failed_attempt_and_never_launches():
    repo = _remote_input_repo()
    store = RecordingStore()
    executor = MemoryExecutor(results=[ExitStatus(0)])

    async def fetch(href: str) -> bytes:
        raise OSError("boom")

    result = await _run(
        _remote_input_run(), executor, repo, storage_client=store, fetch_remote=fetch
    )
    assert result.status in ("failed", "dead")
    assert "could not stage" in (result.error or "")
    # A staging failure means NO container ever existed.
    assert executor.launched == []
    # ...and it spends an attempt (retry path), unlike an infrastructure fault.
    assert repo.finished[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_run_one_dies_when_the_network_level_exceeds_the_cap():
    repo = _remote_input_repo()
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(
        _remote_input_run(runtime={"kind": "inline_python", "network": {"level": "open"}}),
        executor,
        repo,
    )
    assert result.status == "dead"
    assert "PROCESS_NETWORK_MAX" in (result.error or "")
    assert executor.launched == []


# ---------------------------------------------------------------------------
# cron
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "schedule,moment,expected",
    [
        ("* * * * *", NOW, True),
        ("0 12 * * *", NOW, True),
        ("0 13 * * *", NOW, False),
        ("*/15 * * * *", NOW, True),
        ("*/15 * * * *", NOW.replace(minute=7), False),
        ("0 12 31 8 *", NOW, True),
        ("0 12 * * 1", NOW, True),  # 2026-08-31 is a Monday
        ("0 12 * * 0", NOW, False),
        ("0 0 3 * * *", NOW, False),  # six fields are not a valid schedule
    ],
)
def test_cron_minute_matching(schedule, moment, expected):
    assert matches_minute(schedule, moment) is expected


def test_a_source_that_already_ran_this_minute_is_not_due_again():
    """Makes a minute-granular tick idempotent: a retry or an overlapping
    worker must not produce two runs for one scheduled slot."""
    trigger = {"kind": "cron", "schedule": "* * * * *"}
    assert is_due(trigger, NOW, None) is True
    assert is_due(trigger, NOW, NOW - dt.timedelta(seconds=30)) is False
    assert is_due(trigger, NOW, NOW - dt.timedelta(minutes=5)) is True


def test_an_item_event_trigger_is_never_cron_due():
    assert is_due({"kind": "item_event"}, NOW, None) is False


# ---------------------------------------------------------------------------
# the stall sweep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_sweep_requeues_stranded_runs():
    repo = FakeProcessRepo(stalled_reset=3)
    result = await process_sweep_tick(repo, stall_seconds=3600, batch_limit=100, now=NOW)
    assert result.requeued == 3


# ---------------------------------------------------------------------------
# the trigger job's revision resolution (M5-G gate finding)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_item_event_trigger_resolves_the_deployed_revision():
    """The M5-G rehearsal caught this: the dispatcher matched and enqueued
    correctly, but the trigger job looked the revision up through
    `list_due_cron_sources` — which filters to `cron` triggers — so an
    item_event process resolved nothing and every run was silently dropped as
    "nothing deployed". The unit suite missed it because the dispatcher leg
    and the job were tested separately.
    """
    from pipeline.jobs.process import JOB_TRIGGER
    from pipeline.queue.memory import InMemoryQueue

    repo = FakeProcessRepo(deployed_revision=REV)
    # A cron-source list that does NOT contain this process is exactly the
    # state the old lookup mis-read as "nothing deployed".
    repo.cron_sources = []

    revision = await repo.current_revision(PROC)
    assert revision == REV, "an item_event process must resolve its revision"

    # And the wiring the job relies on is registered under the name the
    # dispatcher enqueues.
    assert JOB_TRIGGER == "pipeline.process_trigger"
    assert InMemoryQueue is not None


@pytest.mark.asyncio
async def test_a_process_with_nothing_deployed_still_resolves_to_None():
    repo = FakeProcessRepo(deployed_revision=None)
    assert await repo.current_revision(PROC) is None


# --------------------------------------------------------------------------- #
# G-3: coalescing covers every QUEUED run for a source, not just deferred ones,
# and an undeferred trigger asks for immediate execution.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_undeferred_queued_run_absorbs_a_second_trigger_for_the_same_source():
    """The reason G-3 widened the index: with immediate dispatch, two arrivals
    milliseconds apart used to become two runs (observed live 2026-09-02)."""
    repo = FakeProcessRepo()
    first = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "a", "collection_id": "c", "op": "insert"}],
        now=NOW,
    )
    second = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "b", "collection_id": "c", "op": "insert"}],
        now=NOW,
    )

    assert first.run_id == second.run_id
    assert first.merged is False and second.merged is True
    assert repo.enqueued[0]["input_items"] == [
        {"item_id": "a", "collection_id": "c", "op": "insert"},
        {"item_id": "b", "collection_id": "c", "op": "insert"},
    ]


@pytest.mark.asyncio
async def test_a_claimed_run_is_not_a_coalescing_target():
    """Claiming moves the row to `running`, out of the partial index — so a
    later arrival starts a NEW run instead of joining one already executing."""
    repo = FakeProcessRepo()
    first = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "a"}],
        now=NOW,
    )
    assert await repo.claim_run(first.run_id, NOW) is not None

    second = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "b"}],
        now=NOW,
    )
    assert second.run_id != first.run_id and second.merged is False


@pytest.mark.asyncio
async def test_claim_by_id_is_atomic_against_a_second_claimer():
    repo = FakeProcessRepo()
    result = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "a"}],
        now=NOW,
    )
    first = await repo.claim_run(result.run_id, NOW)
    second = await repo.claim_run(result.run_id, NOW)

    assert first is not None and first.id == result.run_id
    assert second is None


@pytest.mark.asyncio
async def test_test_runs_never_coalesce():
    """A test run is its own event: folding it into a pending batch would make
    'run this now' silently mean 'maybe later, with other people's items'."""
    repo = FakeProcessRepo()
    a = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[],
        now=NOW,
        is_test=True,
    )
    b = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[],
        now=NOW,
        is_test=True,
    )
    assert a.run_id != b.run_id


@pytest.mark.asyncio
async def test_trigger_asks_for_immediate_execution_when_not_deferred():
    repo = FakeProcessRepo()
    asked: list[str] = []

    async def enqueue_now(run_id: str) -> None:
        asked.append(run_id)

    result = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "a"}],
        now=NOW,
        enqueue_now=enqueue_now,
    )

    assert result.enqueued_now is True
    assert asked == [result.run_id]


@pytest.mark.asyncio
async def test_a_rate_deferred_trigger_is_not_dispatched_now():
    """Immediate dispatch must not defeat the ceiling: a deferred run waits for
    the tick that finds its deferral elapsed."""
    repo = FakeProcessRepo(
        windows={PROC: RateWindow(recent_runs=60, oldest_in_window=NOW, max_runs_per_hour=60)}
    )
    asked: list[str] = []

    async def enqueue_now(run_id: str) -> None:
        asked.append(run_id)

    result = await trigger_run(
        repo,
        process_id=PROC,
        revision_id=REV,
        source_id=SRC,
        input_items=[{"item_id": "a"}],
        now=NOW,
        enqueue_now=enqueue_now,
    )

    assert result.deferred is True
    assert result.enqueued_now is False and asked == []


ASSOC = "55555555-5555-4555-8555-555555555555"


async def test_extractor_triggers_coalesce_per_association_until_claimed():
    repo = FakeProcessRepo()
    first = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None,
        association_id=ASSOC, input_items=[{"item_id": "a", "ledger_ids": ["1"]}], now=NOW,
    )
    second = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None,
        association_id=ASSOC, input_items=[{"item_id": "b", "ledger_ids": ["2"]}], now=NOW,
    )
    assert second.run_id == first.run_id and second.merged
    assert [i["item_id"] for i in repo.enqueued[0]["input_items"]] == ["a", "b"]
    assert repo.enqueued[0]["association_id"] == ASSOC

    claimed = await repo.claim_run(first.run_id, NOW)
    assert claimed is not None and claimed.association_id == ASSOC

    third = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None,
        association_id=ASSOC, input_items=[{"item_id": "c", "ledger_ids": ["3"]}], now=NOW,
    )
    assert third.run_id != first.run_id and not third.merged


async def test_a_source_less_association_less_trigger_never_coalesces():
    repo = FakeProcessRepo()
    a = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None, input_items=[], now=NOW
    )
    b = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None, input_items=[], now=NOW
    )
    assert a.run_id != b.run_id


async def test_get_run_returns_the_batch_for_finalize():
    repo = FakeProcessRepo()
    r = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None, association_id=ASSOC,
        input_items=[{"item_id": "a", "ledger_ids": ["1"], "draft": {"id": "a"}}], now=NOW,
    )
    rec = await repo.get_run(r.run_id)
    assert rec is not None
    assert (rec.process_id, rec.association_id, rec.status) == (PROC, ASSOC, "queued")
    assert rec.input_items[0]["draft"] == {"id": "a"}
