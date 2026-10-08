"""FakeCubeRepo mirrors PgCubeRepo's Z-4 semantics (pinned by the DB-gated tests)."""

import datetime as dt

from _cube_fake import FakeCubeRepo, FakeSink
from pipeline.cubes.repo import LedgerEntry, RowOutcome

T0 = dt.datetime(2026, 10, 3, 17, 0, tzinfo=dt.UTC)


async def test_take_finish_and_record_commit():
    repo = FakeCubeRepo(sinks=[FakeSink("s1", "src", "cube", version="v1")])
    await repo.record_appends(
        [LedgerEntry("s1", "b", T0 + dt.timedelta(minutes=5)), LedgerEntry("s1", "a", T0)]
    )
    taken = await repo.take_pending("s1", 50)
    assert [(r.item_id, r.attempts) for r in taken] == [("a", 1), ("b", 1)]
    assert await repo.release_rows("s1", [taken[1].id]) == 1
    assert repo.rows("s1")[1].attempts == 0
    assert await repo.finish_rows("s1", [RowOutcome(taken[0].id, "appended", None, "S1")]) == 1
    assert await repo.has_pending("s1")
    kw = {"appended_at": T0, "source_prefixes": ["s3://b/"]}
    assert not await repo.record_commit("s1", snapshot_id="S1", first_commit_version="v0", **kw)
    assert await repo.record_commit("s1", snapshot_id="S1", first_commit_version="v1", **kw)
    assert not await repo.record_commit("s1", snapshot_id="S2", first_commit_version="v1", **kw)
    sink = await repo.load_sink("s1")
    assert (sink.last_snapshot_id, sink.source_prefixes, sink.version) == ("S1", ("s3://b/",), "v1")

