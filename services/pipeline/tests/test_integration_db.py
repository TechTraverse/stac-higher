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
