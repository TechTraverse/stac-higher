"""build_queue wires the heartbeat, both connection bridge jobs, and cleanup."""

from pipeline.config import Settings
from pipeline.jobs.dispatch import JOB_DISPATCH_POLL
from pipeline.jobs.drain import JOB_NAME as DRAIN_JOB
from pipeline.jobs.gc import COLLECT_JOB_NAME, RETENTION_JOB_NAME
from pipeline.jobs.health_sweep import JOB_NAME as SWEEP_JOB
from pipeline.jobs.heartbeat import JOB_NAME as HEARTBEAT_JOB
from pipeline.jobs.history import JOB_NAME as HISTORY_JOB
from pipeline.jobs.ingest import JOB_DISCOVER, JOB_FETCH, JOB_GROUP, JOB_ITEMIZE, JOB_POLL
from pipeline.jobs.monitor import JOB_NAME as MONITOR_JOB
from pipeline.jobs.process import JOB_CRON, JOB_REAP, JOB_RUN_TICK, JOB_SWEEP
from pipeline.jobs.notify import SWEEP_JOB_NAME as NOTIFY_SWEEP_JOB
from pipeline.jobs.staging_cleanup import JOB_NAME as CLEANUP_JOB
from pipeline.main import build_queue
from pipeline.notify.fanout import WEBHOOK_JOB_NAME


def test_build_queue_registers_all_periodic_jobs():
    # constructing the Procrastinate app opens no DB connections.
    queue = build_queue(Settings.from_env(env={}))
    registered = set(queue.app.tasks)
    assert {HEARTBEAT_JOB, DRAIN_JOB, SWEEP_JOB, CLEANUP_JOB} <= registered
    assert {JOB_POLL, JOB_DISCOVER, JOB_GROUP, JOB_FETCH, JOB_ITEMIZE} <= registered
    assert JOB_DISPATCH_POLL in registered
    assert {MONITOR_JOB, NOTIFY_SWEEP_JOB, WEBHOOK_JOB_NAME} <= registered
    assert {RETENTION_JOB_NAME, COLLECT_JOB_NAME, HISTORY_JOB} <= registered
    # The reaper is its own leg, not folded into the DB sweep (M3-W-1).
    assert {JOB_RUN_TICK, JOB_CRON, JOB_SWEEP, JOB_REAP} <= registered
