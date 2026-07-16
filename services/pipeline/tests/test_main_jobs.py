"""build_queue wires the heartbeat, both connection bridge jobs, and cleanup."""

from pipeline.config import Settings
from pipeline.jobs.drain import JOB_NAME as DRAIN_JOB
from pipeline.jobs.health_sweep import JOB_NAME as SWEEP_JOB
from pipeline.jobs.heartbeat import JOB_NAME as HEARTBEAT_JOB
from pipeline.jobs.staging_cleanup import JOB_NAME as CLEANUP_JOB
from pipeline.main import build_queue


def test_build_queue_registers_all_periodic_jobs():
    # constructing the Procrastinate app opens no DB connections.
    queue = build_queue(Settings.from_env(env={}))
    registered = set(queue.app.tasks)
    assert {HEARTBEAT_JOB, DRAIN_JOB, SWEEP_JOB, CLEANUP_JOB} <= registered
