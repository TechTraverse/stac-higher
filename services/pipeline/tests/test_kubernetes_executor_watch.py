"""K-5 (plan Task 7): KubernetesExecutor.watch — Job and Workload watches as
K-4 wake-ups (identity only), from a listed resourceVersion, with bookmarks,
resume on a normal close, and a raise on 410 / idle / transport errors so
K-4's run_watcher reconnects with a RESYNC."""

from __future__ import annotations

import asyncio

import pytest

from _kube_executor_fixtures import NAME, SCAN, spec
from pipeline.process import kubernetes_executor as kx
from pipeline.process.executor import RESYNC, ExecutorUnavailable, RunEvent
from pipeline.process.kube_api import KubeGone
from pipeline.process.kubernetes_executor import KubernetesExecutor, _job_event, _workload_event
from pipeline.process.watcher import run_watcher

RUN = spec().run_id


@pytest.fixture(autouse=True)
def _fresh_isolation_cache():
    kx.reset_isolation_cache()
    yield
    kx.reset_isolation_cache()


class Consumer:
    """Drains ``executor.watch()`` into a list in a background task."""

    def __init__(self, executor):
        self.events: list = []
        self.error: BaseException | None = None
        self._agen = executor.watch()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        try:
            async for event in self._agen:
                self.events.append(event)
        except Exception as err:
            self.error = err

    async def wait_for(self, predicate, timeout=5.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                raise AssertionError(f"timed out; events={self.events} error={self.error!r}")
            await asyncio.sleep(0.02)

    async def close(self):
        self.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await self.task


def _watches(fake, resource):
    return [c for c in fake.calls if c.verb == "watch" and c.resource == resource]


async def _open(fake, executor, *, workloads=True):
    consumer = Consumer(executor)
    await consumer.wait_for(lambda: consumer.events == [RESYNC])
    want = 2 if workloads else 1
    await consumer.wait_for(lambda: fake.open_watches >= want)
    return consumer


async def test_watch_yields_resync_first_then_job_events(executor, fake):
    consumer = await _open(fake, executor)
    await asyncio.to_thread(executor.submit, spec())
    event = RunEvent(RUN, handle_id=NAME, kind="process")
    await consumer.wait_for(lambda: event in consumer.events)
    before = consumer.events.count(event)
    await asyncio.to_thread(fake.set_job_status, NAME, ready=1)
    await consumer.wait_for(lambda: consumer.events.count(event) > before)
    assert consumer.events[0] == RESYNC
    assert all(e == event for e in consumer.events[1:])  # Job and Workload alike
    await consumer.close()


async def test_workload_events_map_to_their_job(executor, fake):
    await asyncio.to_thread(executor.submit, spec())
    consumer = await _open(fake, executor)
    await asyncio.to_thread(fake.set_workload, NAME, conditions=[
        {"type": "QuotaReserved", "status": "False", "reason": "WaitingForQuota",
         "message": "waiting"},
    ])
    await consumer.wait_for(lambda: len(consumer.events) >= 2)
    assert consumer.events[1] == RunEvent(RUN, handle_id=NAME, kind="process")
    await consumer.close()


async def test_scan_job_events_carry_their_kind(executor, fake):
    consumer = await _open(fake, executor)
    scanner = KubernetesExecutor(api=executor.api, _suffix=lambda: "ffffff")
    await asyncio.to_thread(scanner.submit, spec(
        kind="image_scan", run_id=SCAN, process_id="img", profile=None,
        image="stac-higher-image-scanner:local",
    ))
    want = RunEvent(SCAN, handle_id=f"stac-scan-{SCAN}-ffffff", kind="image_scan")
    await consumer.wait_for(lambda: want in consumer.events)
    await consumer.close()


async def test_the_first_watch_starts_at_the_lists_resource_version(executor, fake):
    await asyncio.to_thread(executor.submit, spec())
    consumer = await _open(fake, executor)
    lists = [c for c in fake.calls if c.verb == "list" and c.resource == "jobs"]
    assert lists[-1].query == {"labelSelector": "stac-higher.run-id", "limit": "1"}
    (job_watch,) = _watches(fake, "jobs")
    assert job_watch.query["allowWatchBookmarks"] == "true"
    assert job_watch.query["timeoutSeconds"] == "300"
    assert job_watch.query["labelSelector"] == "stac-higher.run-id"
    # The list's RV: nothing that existed before is replayed as ADDED.
    assert int(job_watch.query["resourceVersion"]) >= int(
        fake.objects("batch", "jobs")[NAME]["metadata"]["resourceVersion"]
    )
    (wl_watch,) = _watches(fake, "workloads")
    assert "labelSelector" not in wl_watch.query or wl_watch.query["labelSelector"] == ""
    await asyncio.sleep(0.2)
    assert consumer.events == [RESYNC]
    await consumer.close()


async def test_a_normal_close_resumes_from_the_last_bookmark(executor, fake):
    consumer = await _open(fake, executor)
    await asyncio.to_thread(executor.submit, spec())
    await consumer.wait_for(lambda: len(consumer.events) >= 2)
    fake.bookmark()
    await asyncio.sleep(0.2)
    bookmark_rv = str(fake._rv)
    fake.close_watches()
    await consumer.wait_for(lambda: len(_watches(fake, "jobs")) == 2)
    assert _watches(fake, "jobs")[1].query["resourceVersion"] == bookmark_rv
    assert consumer.events.count(RESYNC) == 1 and consumer.error is None
    await consumer.close()


async def test_a_410_ends_the_stream_so_the_watcher_resyncs(executor, fake):
    consumer = await _open(fake, executor)
    await asyncio.to_thread(executor.submit, spec())
    await consumer.wait_for(lambda: len(consumer.events) >= 2)
    fake.compact()
    fake.close_watches()
    await consumer.wait_for(lambda: consumer.task.done())
    assert isinstance(consumer.error, KubeGone)
    await asyncio.sleep(0.1)
    assert fake.open_watches == 0  # the other pump was cancelled and closed


async def test_silence_past_the_idle_timeout_raises(executor, fake, monkeypatch):
    monkeypatch.setattr(kx, "WATCH_IDLE_SECONDS", 0.3)  # K-6 A3: read at call time
    consumer = Consumer(executor)
    await consumer.wait_for(lambda: consumer.task.done())
    assert consumer.events == [RESYNC]
    assert isinstance(consumer.error, ExecutorUnavailable)
    assert "idle" in str(consumer.error)


async def test_the_server_timeout_is_read_at_call_time(executor, fake, monkeypatch):
    monkeypatch.setattr(kx, "WATCH_TIMEOUT_SECONDS", 7)
    consumer = await _open(fake, executor)
    assert _watches(fake, "jobs")[0].query["timeoutSeconds"] == "7"
    await consumer.close()


async def test_a_missing_kueue_api_watches_jobs_only(executor, fake):
    fake.kueue = False
    consumer = await _open(fake, executor, workloads=False)
    await asyncio.to_thread(executor.submit, spec())
    await consumer.wait_for(lambda: len(consumer.events) >= 2)
    assert _watches(fake, "workloads") == []
    assert consumer.error is None
    await consumer.close()


async def test_a_list_that_fails_raises_before_resync(executor, fake):
    fake.fail_next("GET", "jobs", 500)
    consumer = Consumer(executor)
    await consumer.wait_for(lambda: consumer.task.done())
    assert consumer.events == [] and isinstance(consumer.error, ExecutorUnavailable)


async def test_with_run_watcher_every_connect_resyncs(executor, fake):
    seen: list = []

    async def on_event(event):
        seen.append(event)
        if event == RESYNC and seen.count(RESYNC) == 1:
            await asyncio.sleep(0.2)  # let both watches open
            await asyncio.to_thread(executor.submit, spec())

    async def breaker():
        for _ in range(2):
            while seen.count(RESYNC) < _ + 1 or fake.open_watches < 2:
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.3)
            # Move the store past what the watches have seen (pods are not
            # watched), so compaction leaves their resume versions behind.
            await asyncio.to_thread(fake.add_pod, NAME, phase="Pending")
            fake.compact()
            fake.close_watches()

    breaking = asyncio.create_task(breaker())
    await asyncio.wait_for(
        run_watcher(executor.watch, on_event, reconnect_delay=0, max_connects=2), 10
    )
    await breaking
    resyncs = [i for i, e in enumerate(seen) if e == RESYNC]
    assert len(resyncs) == 2
    assert resyncs[0] == 0
    assert RunEvent(RUN, handle_id=NAME, kind="process") in seen[resyncs[0]:resyncs[1]]


def test_strangers_and_garbage_are_ignored():
    labels = {"stac-higher.run-id": RUN, "stac-higher.run-kind": "process"}
    assert _job_event({"metadata": {"name": NAME, "labels": labels}}) == RunEvent(
        RUN, handle_id=NAME, kind="process"
    )
    assert _job_event({"metadata": {"name": "x", "labels": {"app": "y"}}}) is None
    assert _job_event("nope") is None
    assert _job_event({"kind": "Status", "code": 410}) is None
    owner = {"kind": "Job", "name": NAME, "apiVersion": "batch/v1"}
    assert _workload_event({"metadata": {"ownerReferences": [owner]}}) == RunEvent(
        RUN, handle_id=NAME, kind="process"
    )
    assert _workload_event({"metadata": {"ownerReferences": []}}) is None
    assert _workload_event(
        {"metadata": {"ownerReferences": [{**owner, "name": "coredns-abc"}]}}
    ) is None
    assert _workload_event({"metadata": {"ownerReferences": [{**owner, "kind": "Pod"}]}}) is None
    assert _workload_event(None) is None


def test_supports_watch():
    assert KubernetesExecutor.supports_watch.fget(
        KubernetesExecutor.__new__(KubernetesExecutor)
    ) is True


async def test_the_watches_open_before_the_resync_is_handled(executor, fake):
    """Review C4: a long RESYNC sweep must not outlast the watch cache's
    window: the watches start before RESYNC is yielded."""
    agen = executor.watch()
    assert await agen.__anext__() == RESYNC
    await asyncio.sleep(0.3)  # the consumer is still "sweeping"
    assert _watches(fake, "jobs") and _watches(fake, "workloads")
    await agen.aclose()
