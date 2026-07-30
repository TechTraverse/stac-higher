"""Procrastinate backend: registration wiring, no database required.

Constructing the backend and registering tasks must not open connections —
these tests would hang or error otherwise.
"""

import pytest

from pipeline.jobs import heartbeat
from pipeline.queue.interface import RetrySpec
from pipeline.queue.procrastinate_backend import ProcrastinateQueue

DSN = "postgresql://username:password@localhost:5433/postgis"


@pytest.fixture
def queue() -> ProcrastinateQueue:
    return ProcrastinateQueue(DSN, schema="procrastinate_test")


def test_construction_opens_no_connection(queue: ProcrastinateQueue):
    assert queue.name == "procrastinate"
    assert queue.schema == "procrastinate_test"


def test_rejects_unsafe_schema_name():
    with pytest.raises(ValueError):
        ProcrastinateQueue(DSN, schema="bad-schema; DROP TABLE x")


def test_register_task_lands_in_procrastinate_registry(queue: ProcrastinateQueue):
    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.example")
    assert "jobs.example" in queue.app.tasks


def test_register_task_with_retry_maps_to_retry_strategy(queue: ProcrastinateQueue):
    # I-55: a RetrySpec must reach Procrastinate as a real retry strategy —
    # without one, a handler exception fails the job after a single attempt.
    async def handler(**kw):
        pass

    queue.register_task(
        handler, name="jobs.retrying", retry=RetrySpec(max_attempts=4, wait_seconds=60)
    )
    strategy = queue.app.tasks["jobs.retrying"].retry_strategy
    assert strategy is not None
    assert strategy.max_attempts == 4
    assert strategy.wait == 60


def test_register_periodic_lands_in_registry_with_queueing_lock(
    queue: ProcrastinateQueue,
):
    async def tick(timestamp: int):
        pass

    queue.register_periodic(tick, name="jobs.tick", cron="* * * * *")
    task = queue.app.tasks["jobs.tick"]
    assert task.queueing_lock == "jobs.tick"
    # the periodic registry holds our task
    registered = {
        periodic_task.task.name
        for periodic_task in queue.app.periodic_registry.periodic_tasks.values()
    }
    assert "jobs.tick" in registered


def test_heartbeat_registers_through_interface(queue: ProcrastinateQueue):
    heartbeat.register(queue, state=heartbeat.HeartbeatState())
    assert heartbeat.JOB_NAME in queue.app.tasks


async def test_enqueue_batch_empty_is_noop(queue: ProcrastinateQueue):
    # must not touch the (nonexistent) database
    assert await queue.enqueue_batch("jobs.whatever", []) == []
