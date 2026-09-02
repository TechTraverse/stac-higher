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
    #: GOES spec §3: what the planner reads before launch.
    items: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    source_collections: list[str] = field(default_factory=list)
    #: What `current_revision` reports; None models "nothing deployed".
    deployed_revision: str | None = "rev-1"
    #: The revision payload `claim_run` returns with a claimed row.
    runtime: dict[str, Any] = field(default_factory=lambda: {"kind": "inline_python"})
    code: str | None = "pass"

    #: recorded writes
    enqueued: list[dict[str, Any]] = field(default_factory=list)
    finished: list[dict[str, Any]] = field(default_factory=list)
    source_runs: list[tuple[str, bool]] = field(default_factory=list)
    stalled_reset: int = 0
    #: mirrors migration 025's partial unique index: (process, source) → the
    #: QUEUED run id. A claim removes the entry, exactly as flipping the row to
    #: `running` drops it out of the partial index.
    _queued: dict[tuple[str, str], str] = field(default_factory=dict)
    _next_id: int = 0

    async def list_item_event_sources(self, collection_id: str) -> list[ProcessSource]:
        return self.sources.get(collection_id, [])

    async def list_due_cron_sources(self, now: dt.datetime) -> list[CronSource]:
        return list(self.cron_sources)

    async def current_revision(self, process_id: str) -> str | None:
        return self.deployed_revision

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
        run_id, _merged = await self.enqueue_run_detailed(
            process_id=process_id,
            revision_id=revision_id,
            source_id=source_id,
            input_items=input_items,
            deferred_until=deferred_until,
            is_test=is_test,
        )
        return run_id

    async def enqueue_run_detailed(
        self,
        *,
        process_id: str,
        revision_id: str,
        source_id: str | None,
        input_items: Sequence[dict[str, Any]],
        deferred_until: dt.datetime | None,
        is_test: bool = False,
    ) -> tuple[str | None, bool]:
        # Model migration 025: at most one QUEUED run per (process, source),
        # and a second trigger APPENDS to it. Test runs are excluded, as in the
        # real statement.
        if source_id is not None and not is_test:
            existing = self._queued.get((process_id, source_id))
            if existing is not None:
                for row in self.enqueued:
                    if row["run_id"] == existing:
                        row["input_items"] = list(row["input_items"]) + list(input_items)
                return existing, True
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
                "status": "queued",
            }
        )
        if source_id is not None and not is_test:
            self._queued[(process_id, source_id)] = run_id
        return run_id, False

    async def claim_run(self, run_id: str, now: dt.datetime) -> QueuedRun | None:
        for row in self.enqueued:
            if row["run_id"] != run_id or row["status"] != "queued":
                continue
            deferred = row["deferred_until"]
            if deferred is not None and deferred > now:
                return None
            row["status"] = "running"
            # Out of the partial index, so a later trigger starts a new run.
            self._queued.pop((row["process_id"], row["source_id"]), None)
            return QueuedRun(
                id=run_id,
                process_id=row["process_id"],
                revision_id=row["revision_id"],
                source_id=row["source_id"],
                attempts=1,
                input_items=list(row["input_items"]),
                runtime=self.runtime,
                code=self.code,
                env=[],
                is_test=row["is_test"],
            )
        return None

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

    async def list_source_collections(self, process_id: str) -> tuple[str, ...]:
        return tuple(sorted(set(self.source_collections)))

    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        return self.items.get((collection_id, item_id))

    async def reset_stalled_runs(self, older_than: dt.datetime, limit: int) -> int:
        return self.stalled_reset

    async def run_statuses(self, run_ids: Sequence[str]) -> dict[str, str]:
        return {}

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
