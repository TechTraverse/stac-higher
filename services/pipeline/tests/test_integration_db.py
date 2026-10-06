"""Database integration tests — auto-skip unless DATABASE_URL is set.

Run locally against the compose Postgres:

    DATABASE_URL=postgresql://username:password@localhost:5433/postgis \
        uv run pytest tests/test_integration_db.py
"""

import os

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="DATABASE_URL not set — skipping DB integration tests"
)

SCHEMA = "procrastinate_itest"


@pytest.fixture
async def queue():
    import psycopg

    from pipeline.queue.procrastinate_backend import ProcrastinateQueue

    queue = ProcrastinateQueue(DATABASE_URL, schema=SCHEMA)
    yield queue
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, autocommit=True) as conn:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')


async def test_setup_is_idempotent_and_enqueue_works(queue):
    await queue.setup()
    await queue.setup()  # second run must be a no-op, not an error
    await queue.check_connection()

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.itest")
    result = await queue.enqueue("jobs.itest", {"n": 1})
    assert result.job_id is not None and result.job_id.isdigit()
    assert result.coalesced is False

    batch_ids = await queue.enqueue_batch("jobs.itest", [{"n": 2}, {"n": 3}])
    assert len(batch_ids) == 2


async def test_queueing_lock_coalesces_and_lock_reaches_the_row(queue):
    """Spec §5.1 against real Procrastinate: the second defer with the same
    queueing_lock is coalesced (not raised), another lock is accepted, and
    both locks are stored on the job row."""
    import psycopg

    from pipeline.queue.interface import Enqueued

    await queue.setup()

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.locked")
    first = await queue.enqueue("jobs.locked", {"n": 1}, lock="cube:a", queueing_lock="cube:a")
    second = await queue.enqueue("jobs.locked", {"n": 2}, lock="cube:a", queueing_lock="cube:a")
    other = await queue.enqueue("jobs.locked", {"n": 3}, lock="cube:b", queueing_lock="cube:b")
    await queue.aclose()

    assert first.coalesced is False and first.job_id is not None
    assert second == Enqueued(job_id=None, coalesced=True)
    assert other.coalesced is False

    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute(
            f'SELECT lock, queueing_lock FROM "{SCHEMA}".procrastinate_jobs WHERE id = %s',
            (int(first.job_id),),
        )
        assert await cur.fetchone() == ("cube:a", "cube:a")


async def test_retry_stalled_against_real_procrastinate(queue):
    """Decision 14 against real Procrastinate. A 'doing' job with no worker row
    (a dead worker, pruned) is requeued. One whose queueing_lock a waiting job
    already holds is closed as failed. A job on a live worker is left alone."""
    import psycopg

    await queue.setup()

    async def handler(**kw):
        pass

    queue.register_task(handler, name="jobs.locked")
    lone = await queue.enqueue("jobs.locked", {"n": 1}, lock="cube:a", queueing_lock="cube:a")
    covered = await queue.enqueue("jobs.locked", {"n": 2}, lock="cube:b", queueing_lock="cube:b")
    live = await queue.enqueue("jobs.locked", {"n": 3}, lock="cube:c", queueing_lock="cube:c")
    jobs = f'"{SCHEMA}".procrastinate_jobs'
    # Procrastinate's status trigger names its enum type unqualified, so the
    # raw connection needs the queue's search_path, like the backend's pool.
    async with await psycopg.AsyncConnection.connect(
        DATABASE_URL, autocommit=True, options=f"-c search_path={SCHEMA},public"
    ) as conn:
        await conn.execute(
            f"UPDATE {jobs} SET status = 'doing', worker_id = NULL WHERE id = ANY(%s)",
            ([int(lone.job_id), int(covered.job_id)],),
        )
        cur = await conn.execute(
            f'INSERT INTO "{SCHEMA}".procrastinate_workers DEFAULT VALUES RETURNING id'
        )
        worker_id = (await cur.fetchone())[0]
        await conn.execute(
            f"UPDATE {jobs} SET status = 'doing', worker_id = %s WHERE id = %s",
            (worker_id, int(live.job_id)),
        )
    # 'covered' is doing, so it holds no queueing lock: a new wake is accepted.
    waiting = await queue.enqueue("jobs.locked", {"n": 4}, lock="cube:b", queueing_lock="cube:b")
    assert waiting.coalesced is False

    assert await queue.retry_stalled("jobs.locked") == 2
    await queue.aclose()

    by_name = {"lone": lone, "covered": covered, "live": live, "waiting": waiting}
    ids = {int(result.job_id): name for name, result in by_name.items()}
    async with await psycopg.AsyncConnection.connect(DATABASE_URL) as conn:
        cur = await conn.execute(
            f"SELECT id, status::text FROM {jobs} WHERE id = ANY(%s)", (list(ids),)
        )
        statuses = {ids[row[0]]: row[1] for row in await cur.fetchall()}
    assert statuses == {"lone": "todo", "covered": "failed", "live": "doing", "waiting": "todo"}
