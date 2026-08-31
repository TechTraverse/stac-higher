"""M2-G history retention sweep rules (spec §6, ADR 0012)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pipeline.history.sweep import HistoryRepo, history_tick


@dataclass
class FakeHistoryRepo(HistoryRepo):
    calls: list[tuple[str, int | None]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    async def delete_old_connection_checks(self, days: int) -> int:
        self.calls.append(("checks", days))
        return self.counts.get("checks", 0)

    async def fail_stranded_checks(self) -> int:
        self.calls.append(("stranded", None))
        return self.counts.get("stranded", 0)

    async def delete_dead_association_ingest_files(self, days: int) -> int:
        self.calls.append(("ingest", days))
        return self.counts.get("ingest", 0)

    async def delete_dead_association_delivery_log(self, days: int) -> int:
        self.calls.append(("delivery_dead_assoc", days))
        return self.counts.get("delivery_dead_assoc", 0)

    async def delete_itemless_terminal_deliveries(self, days: int) -> int:
        self.calls.append(("delivery_itemless", days))
        return self.counts.get("delivery_itemless", 0)

    async def delete_terminal_staged_uploads(self, days: int) -> int:
        self.calls.append(("staged_uploads", days))
        return self.counts.get("staged_uploads", 0)


async def test_tick_applies_the_right_window_to_each_table():
    repo = FakeHistoryRepo(
        counts={
            "checks": 5,
            "stranded": 2,
            "ingest": 3,
            "delivery_dead_assoc": 1,
            "delivery_itemless": 4,
            "staged_uploads": 6,
        }
    )
    result = await history_tick(repo, checks_days=30, history_days=365)

    assert ("checks", 30) in repo.calls
    assert ("ingest", 365) in repo.calls
    assert ("delivery_dead_assoc", 365) in repo.calls
    assert ("delivery_itemless", 365) in repo.calls
    # staged_uploads (P7-H) shares the conservative history window.
    assert ("staged_uploads", 365) in repo.calls
    assert result.checks_deleted == 5
    assert result.checks_failed_stranded == 2
    assert result.ingest_files_deleted == 3
    # Both delivery prongs are summed into one reported count.
    assert result.delivery_log_deleted == 5
    assert result.staged_uploads_deleted == 6


async def test_stranded_checks_are_failed_before_the_age_prune():
    # Order matters only in that the stranded flip must happen (an aged-out
    # stranded row being deleted instead is fine); assert both ran.
    repo = FakeHistoryRepo()
    await history_tick(repo, checks_days=30, history_days=365)
    kinds = [c[0] for c in repo.calls]
    assert kinds.index("stranded") < kinds.index("checks")
