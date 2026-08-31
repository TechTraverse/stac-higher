"""In-memory ProcessRepo for M5-C unit tests."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline.process.matcher import ProcessSource
from pipeline.process.repo import (
    CronSource,
    ProcessCheckRequest,
    ProcessRepo,
    QueuedRun,
    RateWindow,
)


@dataclass
class FakeProcessRepo(ProcessRepo):
    sources: dict[str, list[ProcessSource]] = field(default_factory=dict)
    cron_sources: list[CronSource] = field(default_factory=list)
    windows: dict[str, RateWindow] = field(default_factory=dict)
    due_runs: list[QueuedRun] = field(default_factory=list)
    checks: list[ProcessCheckRequest] = field(default_factory=list)
    output_collections: list[str] = field(default_factory=list)

    #: recorded writes
    enqueued: list[dict[str, Any]] = field(default_factory=list)
    finished: list[dict[str, Any]] = field(default_factory=list)
    source_runs: list[tuple[str, bool]] = field(default_factory=list)
    stalled_reset: int = 0
    #: mirrors the partial unique index: source_id → deferred run id
    _deferred: dict[str, str] = field(default_factory=dict)
    _next_id: int = 0

    async def list_item_event_sources(self, collection_id: str) -> list[ProcessSource]:
        return self.sources.get(collection_id, [])

    async def list_due_cron_sources(self, now: dt.datetime) -> list[CronSource]:
        return list(self.cron_sources)

    async def rate_window(self, process_id: str, since: dt.datetime) -> RateWindow:
        return self.windows.get(
            process_id, RateWindow(recent_runs=0, oldest_in_window=None, max_runs_per_hour=60)
        )

    async def enqueue_run(
        self,
        *,
        process_id: str,
        revision_id: str,
        source_id: str | None,
        input_items: Sequence[dict[str, Any]],
        deferred_until: dt.datetime | None,
        is_test: bool = False,
    ) -> str | None:
        # Model the migration-022 partial unique index: at most one deferred
        # queued run per source, and a second defer APPENDS to it.
        if deferred_until is not None and source_id is not None:
            existing = self._deferred.get(source_id)
            if existing is not None:
                for row in self.enqueued:
                    if row["run_id"] == existing:
                        row["input_items"] = list(row["input_items"]) + list(input_items)
                return existing
        self._next_id += 1
        run_id = f"run-{self._next_id}"
        self.enqueued.append(
            {
                "run_id": run_id,
                "process_id": process_id,
                "revision_id": revision_id,
                "source_id": source_id,
                "input_items": list(input_items),
                "deferred_until": deferred_until,
                "is_test": is_test,
            }
        )
        if deferred_until is not None and source_id is not None:
            self._deferred[source_id] = run_id
        return run_id

    async def claim_due_runs(self, now: dt.datetime, limit: int) -> list[QueuedRun]:
        batch = self.due_runs[:limit]
        self.due_runs = self.due_runs[limit:]
        return batch

    async def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None,
        log_ref: str | None,
        next_attempt_at: dt.datetime | None,
        output_items: Sequence[dict[str, Any]] | None = None,
    ) -> None:
        self.finished.append(
            {
                "run_id": run_id,
                "status": status,
                "error": error,
                "log_ref": log_ref,
                "next_attempt_at": next_attempt_at,
            }
        )

    async def list_output_collections(self, process_id: str) -> tuple[str, ...]:
        return tuple(self.output_collections)

    async def reset_stalled_runs(self, older_than: dt.datetime, limit: int) -> int:
        return self.stalled_reset

    async def record_source_run(
        self, source_id: str, *, succeeded: bool, at: dt.datetime
    ) -> None:
        self.source_runs.append((source_id, succeeded))

    async def claim_process_checks(self, limit: int) -> list[ProcessCheckRequest]:
        batch = self.checks[:limit]
        self.checks = self.checks[limit:]
        return batch

    async def attach_check_run(self, check_id: str, run_id: str) -> None:
        return None

    async def finish_check(
        self, check_id: str, *, status: str, result: dict[str, Any] | None
    ) -> None:
        return None
