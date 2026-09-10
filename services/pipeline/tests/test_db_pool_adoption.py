"""M3-B: no pipeline repo opens a raw connection any more.

A fourteen-file sweep is exactly the change where one file gets missed, and a
missed repo is invisible — it works, it is just slow, and it stays slow until
someone re-measures under load. This is the cheap guard.
"""

from __future__ import annotations

import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "pipeline"

#: The modules that legitimately open a connection themselves, and why.
#: (Global Constraints lists the same four exemptions with the full reasoning.)
ALLOWED = {
    # Procrastinate owns its own pool; setup() runs before anything else
    # exists and check_connection() IS the health probe — it must reach the
    # database, not report a pool's cached liveness.
    "queue/procrastinate_backend.py",
    # A dedicated autocommit connection held open for LISTEN "item_events".
    "dispatcher/listener.py",
    # M3-A's drainer: `CALL pgstac.run_queued_queries()` COMMITs inside itself
    # and cannot run in a transaction block, so it needs an AUTOCOMMIT
    # connection — which is exactly what the transactional repo pool is not.
    "stac/query_queue.py",
}

#: The twelve repo seams this slice moves onto the pool.
POOLED_REPOS = {
    "connections/repo.py",
    "delivery/backfill.py",
    "delivery/repo.py",
    "dispatcher/repo.py",
    "finalize/repo.py",
    "flow/daily_repo.py",
    "flow/repo.py",
    "gc/repo.py",
    "history/sweep.py",
    "ingest/repo.py",
    "notify/repo.py",
    "process/repo.py",
}


def _relative(path: pathlib.Path) -> str:
    return path.relative_to(SRC).as_posix()


def test_no_repo_opens_a_raw_async_connection():
    offenders = sorted(
        _relative(path)
        for path in SRC.rglob("*.py")
        if "AsyncConnection.connect(" in path.read_text()
        and _relative(path) not in ALLOWED
    )
    assert offenders == [], (
        "these modules still fork a connection per statement instead of "
        f"checking out of pipeline.db.pool: {offenders}"
    )


def test_every_pooled_repo_helper_checks_out_of_the_pool():
    for rel in sorted(POOLED_REPOS):
        text = (SRC / rel).read_text()
        assert "async def _connect(self)" in text, f"{rel}: the seam was renamed"
        assert "get_async_pool" in text, f"{rel}: still not on the pool"
        assert "AsyncConnection.connect(" not in text, f"{rel}: raw connect left behind"


def test_the_exempt_drainer_is_still_autocommit():
    """M3-A's query_queue repo must NOT be swept onto the transactional pool."""
    text = (SRC / "stac/query_queue.py").read_text()
    assert "AsyncConnection.connect(self.database_url, autocommit=True)" in text
    assert "get_async_pool" not in text
