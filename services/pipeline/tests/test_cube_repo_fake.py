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




async def test_maintenance_methods():
    repo = FakeCubeRepo(
        sinks=[
            FakeSink("s2", "src", "c2"),
            FakeSink("s1", "src", "c1", last_snapshot_id="S1"),
            FakeSink("off", "src", "c3", enabled=False),
        ]
    )
    assert await repo.maintainable_sinks() == ["s1", "s2"]
    assert not await repo.record_snapshot("s1", snapshot_id="T1", from_snapshot_id="S0")
    assert await repo.record_snapshot("s1", snapshot_id="T1", from_snapshot_id="S1")
    assert (await repo.load_sink("s1")).last_snapshot_id == "T1"
    await repo.record_appends([LedgerEntry("s1", "a", T0, status="skipped", reason="late")])
    repo.ledger[("s1", "a")].updated_at -= dt.timedelta(days=8)
    await repo.record_appends([LedgerEntry("s1", "b", T0)])
    repo.ledger[("s1", "b")].updated_at -= dt.timedelta(days=8)
    assert await repo.prune_ledger("s1", 7) == 1
    assert [r.item_id for r in repo.rows("s1")] == ["b"]
    await repo.record_maintenance("s1", summary={"status": "ok"}, maintained_at=T0)
    await repo.record_maintenance("s1", summary={"status": "failed"}, maintained_at=None)
    assert (repo.sinks[1].last_maintenance, repo.sinks[1].last_maintained_at) == (
        {"status": "failed"},
        T0,
    )
