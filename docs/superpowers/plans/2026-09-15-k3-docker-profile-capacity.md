# K-3 · DockerExecutor Honours the Profile — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run's hardware block reaches the Docker backend — `NanoCpus` from `cpu`, `DeviceRequests` from the profile's docker block when the host exposes an NVIDIA runtime — and a profile's local `capacity` is enforced: a claimed run whose profile is already full is released back to `queued` as *Waiting for capacity* (`phase = pending_capacity`, retried in 15 s, no attempt spent). Migration 029 lands the ledger columns the submit-then-reconcile executor (K-4) will fill.

**Architecture:** The app owns the DDL (ADR 0001): migration `029_process_runs_executor_phase` adds `executor_backend`, `executor_handle`, `phase` (CHECK `pending_capacity | starting | running`), `phase_detail`, `submitted_at`, `cancel_requested_at` — and, by lead ruling, `next_attempt_at` (the honest "claimable again at" for a queued row, which the capacity release needs today and K-4's credential-expiry requeue needs next; `rate_deferred_until` cannot carry it because migration 025's unique index makes a deferred row per source unique). The pipeline reads the profile's `backend.docker` block through one lenient parser (`docker_backend(profile) -> DockerBackend`), the claim statements set `phase = 'starting'` and honour `next_attempt_at`, `run_one` checks capacity right after the bounds check (`count_running_on_profile` joins `process_revisions` — no new column carries the profile), and `DockerExecutor.launch` adds `NanoCpus` and `DeviceRequests`, probing `GET /info` once for an `nvidia` runtime (the socket proxy gains `INFO=1`, a read-only endpoint). The app's run API returns `phase` / `phase_detail`; `RunRow` shows a phase chip. `cancelled` and the cancel verb wait for K-4.

**Tech Stack:** Python 3.12 (psycopg 3, urllib against the Engine API v1.43), pytest; Astro 7 + React 19, vitest; migration in `app/src/lib/db/migrate.ts`; a contract fixture consumed by both suites.

**Spec:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §5.2 (migration 029), §8 (DockerExecutor: `NanoCpus`, `DeviceRequests`, capacity → `pending_capacity` + 15 s), §3.4 (the docker backend block), §9 (run-row `phase` chip — the chip only; Cancel/`cancelled` are K-4); ADR 0019; `TODO.md` K queue "K-3 · DockerExecutor honours the profile".

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/k3-docker-profile -b ai/k3-docker-profile ai/main` — only after **K-2 has merged into `ai/main`** (Task 0 checks: `RunsCard` exported from `ProcessDetailPage.tsx` and `hardware-public.ts` present). Then `npm install` at the worktree root.
- **Gates:** app tasks `npm run verify` from the worktree root; pipeline tasks `uv run pytest -q` and `uv run ruff check .` from `services/pipeline/`; a task that touches both runs both. Teammates never run e2e, the dev server, or Docker (image builds included).
- **Migration 029 (spec §5.2, amended by ruling):** `ALTER TABLE stac_higher.process_runs ADD COLUMN IF NOT EXISTS` for `executor_backend text`, `executor_handle text`, `phase text`, `phase_detail text`, `submitted_at timestamptz`, `cancel_requested_at timestamptz`, `next_attempt_at timestamptz`; a CHECK constraint `process_runs_phase_check` `(phase IS NULL OR phase IN ('pending_capacity','starting','running'))`. The `status` CHECK is NOT changed (`cancelled` is K-4, migration 030). Never reorder `MIGRATIONS`; 029 follows 028. ADR 0001: the pipeline runs no DDL.
- **Phase vocabulary, both sides, pinned by `tests/contract-fixtures/process-run-phase.json`:** `pending_capacity` (queued, released for capacity), `starting` (claimed, not yet reported running by the backend), `running`. This slice writes `pending_capacity` and `starting`; `running` is written by K-4's watcher. `phase` is NULL on every terminal row and on a plain queued row.
- **Capacity (spec §8):** `backend.docker.capacity` — concurrent runs on this profile, this host; absent or null = unbounded. The claim path (in `run_one`, after the bounds check and before input planning) counts `running` rows whose revision's `runtime->'hardware'->>'profile'` (default `standard`) equals the profile, excluding the run itself; when `count >= capacity` the run is released: `status = 'queued'`, `started_at = NULL`, `attempts = attempts - 1`, `phase = 'pending_capacity'`, `phase_detail = "profile 'P' is at its capacity of N on this host"`, `next_attempt_at = now + 15 s`. No attempt spent, nothing launched, no credentials minted.
- **Claim predicates:** both `claim_due_runs` and `claim_run` add `AND (r.next_attempt_at IS NULL OR r.next_attempt_at <= %(now)s)` and set `phase = 'starting', phase_detail = NULL, next_attempt_at = NULL` on the claimed row. `finish_run` and `reset_stalled_runs` set `phase = NULL, phase_detail = NULL`.
- **DockerExecutor (spec §8):** `HostConfig.NanoCpus = int(round(spec.cpu * 1_000_000_000))`; when `spec.gpu_count > 0`: if the host's `GET /info` reports an `nvidia` key under `Runtimes`, `HostConfig.DeviceRequests` = the profile's `backend.docker.device_requests` entries, each copied with `Count = spec.gpu_count`; otherwise the launch raises `HardwareUnavailable` (a new `ExecutorError` subclass, NOT `ExecutorUnavailable`) with the message `profile 'P' needs an NVIDIA container runtime this Docker host does not expose` and `run_one` marks the run `dead` with that message. When `spec.gpu_count == 0` no `DeviceRequests` key is sent and `/info` is never called. The probe result is cached on the executor instance.
- **Socket proxy:** `docker-socket-proxy` gains `- INFO=1` (read-only `GET /info`), with a comment naming K-3. Nothing else widens.
- **Run API (`GET /api/processes/[id]/runs`)** rows gain `phase: "pending_capacity" | "starting" | "running" | null` and `phase_detail: string | null`. The UI chip: `queued` + `pending_capacity` → `Waiting for capacity` (Badge `outline`, `title={phase_detail}`); `running` + `starting` → `Starting`; `running` + `running` → nothing extra (the status badge already says running).
- **Do not touch:** the finalize path, `delivery/`, the `procrastinate` schema, the pgstac writer, `RunSpec`'s existing fields. No new dependency. Structured logging: data in `extra={...}`, messages constant; never `filename`, `module`, `name`, `msg`, `args`, `levelname` as `extra` keys.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL or name>
  ```

---

### Task 0: Precondition

- [ ] On `ai/main`: `grep -n "export function RunsCard" app/src/components/processes/ProcessDetailPage.tsx` and `ls app/src/lib/processes/hardware-public.ts` both succeed (K-2 merged); `grep -n '"028_builtin_processes"' app/src/lib/db/migrate.ts` is the last migration name. If K-2 is not merged, STOP.

### Task 1: The phase fixture, migration 029, the app's run row

**Files:**
- Create: `tests/contract-fixtures/process-run-phase.json`
- Modify: `tests/contract-fixtures/README.md` (one row in its fixture table)
- Modify: `app/src/lib/db/migrate.ts` (append 029 after 028)
- Modify: `app/src/lib/processes/storage.ts:718-780` (`RUN_COLUMNS`, `RunRow`, `ApiProcessRun`, `toApiRun` — export `toApiRun`)
- Test: `app/src/__tests__/processes-migration.test.ts` (append), `app/src/__tests__/processes-run-phase.test.ts` (new)

**Interfaces:**
- Produces:
  ```json
  // tests/contract-fixtures/process-run-phase.json
  { "phases": ["pending_capacity", "starting", "running"] }
  ```
  ```ts
  // storage.ts
  export type ProcessRunPhase = "pending_capacity" | "starting" | "running";
  export interface ApiProcessRun { …existing…; phase: ProcessRunPhase | null; phase_detail: string | null }
  export function toApiRun(row: RunRow): ApiProcessRun;
  ```
  Migration name: `029_process_runs_executor_phase`.

- [ ] **Step 1: The fixture**

`tests/contract-fixtures/process-run-phase.json`:
```json
{
  "$comment": "K-3 (process-compute spec §5.2): the sub-states of a run the pipeline writes to process_runs.phase and the app's CHECK constraint and UI read. pending_capacity = queued, released because its profile is full; starting = claimed, not yet reported running; running = the backend reported it (K-4's watcher).",
  "phases": ["pending_capacity", "starting", "running"]
}
```
Add a row to the fixture table in `tests/contract-fixtures/README.md`: `| process-run-phase.json | The \`process_runs.phase\` vocabulary (K-3) | app: migration 029's CHECK + \`ProcessRunPhase\`; pipeline: \`process/ledger.py\` \`PHASES\` |` (match the table's actual columns — read the README first).

- [ ] **Step 2: Write the failing migration test**

Append to `app/src/__tests__/processes-migration.test.ts`:
```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const PHASES: string[] = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../tests/contract-fixtures/process-run-phase.json", import.meta.url)),
    "utf8",
  ),
).phases;

describe("migration 029 (executor phase columns — K-3)", () => {
  const sql = migrationEntry("029_process_runs_executor_phase");

  it("runs after X-4's 028", () => {
    expect(migrate.indexOf('"029_process_runs_executor_phase"')).toBeGreaterThan(
      migrate.indexOf('"028_builtin_processes"'),
    );
  });

  it("adds the six spec §5.2 columns plus next_attempt_at, all nullable", () => {
    for (const column of [
      "executor_backend text",
      "executor_handle text",
      "phase text",
      "phase_detail text",
      "submitted_at timestamptz",
      "cancel_requested_at timestamptz",
      "next_attempt_at timestamptz",
    ]) {
      expect(sql).toContain(`ADD COLUMN IF NOT EXISTS ${column}`);
      expect(sql).not.toContain(`${column} NOT NULL`);
    }
  });

  it("constrains phase to exactly the fixture's vocabulary", () => {
    const check = sql.match(/phase IN \(([^)]+)\)/);
    expect(check).not.toBeNull();
    const listed = check![1].split(",").map((s) => s.trim().replace(/'/g, ""));
    expect(listed).toEqual(PHASES);
    expect(sql).toContain("phase IS NULL OR phase IN");
  });

  it("does not touch the status CHECK — cancelled is K-4's", () => {
    expect(sql).not.toContain("cancelled");
  });
});
```
(If the file already imports `readFileSync`/`fileURLToPath`, do not duplicate the imports; `migrate` and `migrationEntry` are the file's existing helpers.)

- [ ] **Step 3: Run it to verify it fails**

Run (from `app/`): `npx vitest run src/__tests__/processes-migration.test.ts`
Expected: FAIL — `migrationEntry` throws on the unknown name.

- [ ] **Step 4: The migration**

Append to `MIGRATIONS` in `app/src/lib/db/migrate.ts`, after the 028 entry:
```ts
  {
    // K-3 (process-compute spec §5.2): the executor's view of a run, filled
    // by the submit-then-reconcile executor (K-4) — this slice writes only
    // `phase` / `phase_detail` / `next_attempt_at`. `next_attempt_at` is the
    // honest "claimable again at" for a QUEUED row (a capacity release now,
    // K-4's credential-expiry requeue next); `rate_deferred_until` cannot
    // carry it because 025's partial unique index allows one deferred row
    // per source. The status CHECK is untouched: `cancelled` lands with the
    // cancel verb (K-4, migration 030).
    name: "029_process_runs_executor_phase",
    sql: `
      ALTER TABLE stac_higher.process_runs
        ADD COLUMN IF NOT EXISTS executor_backend text,
        ADD COLUMN IF NOT EXISTS executor_handle text,
        ADD COLUMN IF NOT EXISTS phase text,
        ADD COLUMN IF NOT EXISTS phase_detail text,
        ADD COLUMN IF NOT EXISTS submitted_at timestamptz,
        ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz,
        ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz;
      DO $do$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint
          WHERE conname = 'process_runs_phase_check'
        ) THEN
          ALTER TABLE stac_higher.process_runs
            ADD CONSTRAINT process_runs_phase_check
            CHECK (phase IS NULL OR phase IN ('pending_capacity','starting','running'));
        END IF;
      END
      $do$;
    `,
  },
```

- [ ] **Step 5: The app's run row**

In `app/src/lib/processes/storage.ts`:
- `RUN_COLUMNS` gains `phase, phase_detail` (after `is_test`).
- `interface RunRow` gains `phase: string | null; phase_detail: string | null;`.
- Above `ApiProcessRun`: `export type ProcessRunPhase = "pending_capacity" | "starting" | "running";` and `ApiProcessRun` gains
  ```ts
  /** K-3: the sub-state of a queued/running row — `pending_capacity` while
   * its profile is full on this host, `starting` once claimed; null on
   * terminal rows. The fixture `process-run-phase.json` pins the values. */
  phase: ProcessRunPhase | null;
  phase_detail: string | null;
  ```
- `toApiRun` becomes `export function toApiRun(...)` and maps `phase: (row.phase as ProcessRunPhase | null) ?? null, phase_detail: row.phase_detail ?? null`.
- `app/src/lib/processes/types.ts`: also re-export the type: `export type { ProcessRunPhase } from "./storage";`.

New test `app/src/__tests__/processes-run-phase.test.ts`:
```ts
// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { toApiRun, type ProcessRunPhase } from "@/lib/processes/storage";

const PHASES: ProcessRunPhase[] = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../tests/contract-fixtures/process-run-phase.json", import.meta.url)),
    "utf8",
  ),
).phases;

const row = {
  id: "r1", process_id: "p1", revision_id: "v1", source_id: null, status: "queued" as const,
  attempts: 0, input_items: [], output_items: [], log_ref: null, error: null,
  rate_deferred_until: null, is_test: false, created_at: "2026-09-15T00:00:00Z",
  started_at: null, finished_at: null, phase: null, phase_detail: null,
};

describe("run rows carry the K-3 phase", () => {
  it("maps every fixture phase and its detail through", () => {
    for (const phase of PHASES) {
      const api = toApiRun({ ...row, phase, phase_detail: `detail for ${phase}` });
      expect(api.phase).toBe(phase);
      expect(api.phase_detail).toBe(`detail for ${phase}`);
    }
  });
  it("is null on a row without one", () => {
    expect(toApiRun(row).phase).toBeNull();
    expect(toApiRun(row).phase_detail).toBeNull();
  });
});
```
(`storage.ts` imports the DB client at module level; if importing it in a node-environment test pulls a connection, mock `@/lib/db/client` the way `api-processes.test.ts` does — read that file's mock block and copy it.)

- [ ] **Step 6: Run the tests and the gate**

Run: `npx vitest run src/__tests__/processes-migration.test.ts src/__tests__/processes-run-phase.test.ts` → PASS.
Run: `npm run verify` from the worktree root → green.

- [ ] **Step 7: Commit**

```bash
git add tests/contract-fixtures/process-run-phase.json tests/contract-fixtures/README.md app/src/lib/db/migrate.ts app/src/lib/processes/storage.ts app/src/lib/processes/types.ts app/src/__tests__/processes-migration.test.ts app/src/__tests__/processes-run-phase.test.ts
git commit -m "feat(processes): migration 029 — executor phase columns + next_attempt_at; run API returns phase/phase_detail; process-run-phase fixture (K-3)"
```

### Task 2: The docker backend block, the ledger phase, per-profile capacity in `run_one`

**Files:**
- Modify: `services/pipeline/src/pipeline/process/hardware.py` (append `DockerBackend`, `docker_backend`)
- Modify: `services/pipeline/src/pipeline/process/ledger.py` (append `PHASES`, `PHASE_PENDING_CAPACITY`, `PHASE_STARTING`, `CAPACITY_RETRY_SECONDS = 15`)
- Modify: `services/pipeline/src/pipeline/process/repo.py` — ABC: `count_running_on_profile`, `release_for_capacity`; `PgProcessRepo`: both, plus the claim / finish / stall statements
- Modify: `services/pipeline/tests/_process_fake.py` — the two new methods; `claim_run` sets `row["phase"] = "starting"`
- Modify: `services/pipeline/src/pipeline/process/runner.py` — the capacity check after the bounds check
- Test: `services/pipeline/tests/test_process_hardware.py` (append), `services/pipeline/tests/test_process_capacity.py` (new)

**Interfaces:**
- Consumes: `HardwareProfile.backend: dict[str, Any]` (K-1), `QueuedRun`, `FakeProcessRepo.enqueued` rows (`{"run_id", "status", "process_id", …}`) and `.finished`.
- Produces:
  ```python
  # hardware.py
  @dataclass(frozen=True)
  class DockerBackend:
      device_requests: tuple[dict[str, Any], ...] = ()
      capacity: int | None = None          # None = unbounded
  def docker_backend(profile: HardwareProfile) -> DockerBackend   # lenient: missing block → defaults; raises HardwareProfileError on a wrong type

  # ledger.py
  PHASES = ("pending_capacity", "starting", "running")
  PHASE_PENDING_CAPACITY, PHASE_STARTING, PHASE_RUNNING = PHASES
  CAPACITY_RETRY_SECONDS = 15
  def capacity_detail(profile_id: str, capacity: int) -> str   # "profile 'P' is at its capacity of N on this host"

  # repo.py (ProcessRepo ABC)
  async def count_running_on_profile(self, profile_id: str, *, exclude_run_id: str) -> int
  async def release_for_capacity(self, run_id: str, *, next_attempt_at: dt.datetime, detail: str) -> None
  ```

- [ ] **Step 1: Failing tests — the backend parser and the ledger vocabulary**

Append to `services/pipeline/tests/test_process_hardware.py`:
```python
def test_docker_backend_reads_capacity_and_device_requests():
    from pipeline.process.hardware import docker_backend

    profile = parse_hardware_profiles(
        {
            "version": 1,
            "profiles": [
                {
                    "id": "standard", "label": "S", "description": "", "tier": "cpu",
                    "accelerator": None, "cpu": {"min": 1, "max": 1, "default": 1},
                    "memory_mb": {"min": 128, "max": 512, "default": 512}, "gpu_count": None,
                    "max_queue_wait_seconds": 60, "image": None,
                    "backend": {"docker": {"device_requests": [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}], "capacity": 2}},
                }
            ],
        }
    ).standard
    backend = docker_backend(profile)
    assert backend.capacity == 2
    assert backend.device_requests == ({"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]},)


def test_docker_backend_is_lenient_about_an_absent_block():
    from pipeline.process.hardware import docker_backend

    profile = parse_hardware_profiles(SAMPLE_WITH_EMPTY_BACKEND).standard  # see below
    assert docker_backend(profile) == DockerBackend()


@pytest.mark.parametrize("bad", [{"capacity": "two"}, {"capacity": 0}, {"device_requests": "gpu"}, "docker"])
def test_docker_backend_rejects_a_malformed_block(bad):
    from pipeline.process.hardware import docker_backend

    doc = json.loads(json.dumps(SAMPLE_WITH_EMPTY_BACKEND))
    doc["profiles"][0]["backend"] = {"docker": bad}
    profile = parse_hardware_profiles(doc).standard
    with pytest.raises(HardwareProfileError):
        docker_backend(profile)


def test_phases_match_the_fixture():
    from pipeline.process.ledger import PHASES

    fixture = json.loads((FIXTURES / "process-run-phase.json").read_text())
    assert list(PHASES) == fixture["phases"]
```
where `SAMPLE_WITH_EMPTY_BACKEND` is a module-level dict identical to the first test's document but with `"backend": {}`, and `FIXTURES` is the path constant the file already uses for `hardware-profiles.json` (read the file's top; reuse its name). Import `DockerBackend` beside `docker_backend`.

- [ ] **Step 2: Run to verify they fail**

Run (from `services/pipeline/`): `uv run pytest tests/test_process_hardware.py -q`
Expected: FAIL — `ImportError: cannot import name 'docker_backend'` / `PHASES`.

- [ ] **Step 3: Implement the parser and the vocabulary**

Append to `hardware.py`:
```python
@dataclass(frozen=True)
class DockerBackend:
    """A profile's `backend.docker` block (spec §3.4): the device requests the
    executor sends when the run asks for GPUs, and how many runs on this
    profile one host runs at once (None = unbounded)."""

    device_requests: tuple[dict[str, Any], ...] = ()
    capacity: int | None = None


def docker_backend(profile: HardwareProfile) -> DockerBackend:
    """Read the docker block leniently: an absent block is the defaults, a
    present-but-wrong one is a `HardwareProfileError` naming the profile."""
    raw = profile.backend.get("docker")
    if raw is None:
        return DockerBackend()
    what = f"profile {profile.id!r}: backend.docker"
    if not isinstance(raw, dict):
        raise HardwareProfileError(f"{what} must be an object")
    requests = raw.get("device_requests") or []
    if not isinstance(requests, list) or not all(isinstance(r, dict) for r in requests):
        raise HardwareProfileError(f"{what}.device_requests must be a list of objects")
    capacity = raw.get("capacity")
    if capacity is not None and (
        isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1
    ):
        raise HardwareProfileError(f"{what}.capacity must be a positive integer or null")
    return DockerBackend(
        device_requests=tuple(dict(r) for r in requests),
        capacity=capacity,
    )
```
Append to `ledger.py`:
```python
#: K-3 (spec §5.2): the sub-states of a queued/running row, pinned by
#: tests/contract-fixtures/process-run-phase.json. `running` is K-4's.
PHASES = ("pending_capacity", "starting", "running")
PHASE_PENDING_CAPACITY, PHASE_STARTING, PHASE_RUNNING = PHASES

#: How long a run released for capacity waits before the tick may claim it
#: again (spec §8).
CAPACITY_RETRY_SECONDS = 15


def capacity_detail(profile_id: str, capacity: int) -> str:
    """The `phase_detail` a capacity release records — what the UI's
    *Waiting for capacity* tooltip shows."""
    return f"profile {profile_id!r} is at its capacity of {capacity} on this host"
```

Run the hardware tests again → PASS.

- [ ] **Step 4: Failing tests — the capacity path in `run_one`**

New `services/pipeline/tests/test_process_capacity.py`:
```python
"""K-3: per-profile capacity on the Docker host (spec §8).

A claimed run whose profile already has `capacity` runs `running` on this host
is released back to `queued` as `pending_capacity`, retried in 15 s, with no
attempt spent and nothing launched.
"""

from __future__ import annotations

import datetime as dt

import pytest

from pipeline.config import Settings
from pipeline.process.executor import ExitStatus
from pipeline.process.hardware import parse_hardware_profiles
from pipeline.process.ledger import CAPACITY_RETRY_SECONDS, PHASE_PENDING_CAPACITY
from pipeline.process.memory_executor import MemoryExecutor
from pipeline.process.repo import QueuedRun
from pipeline.process.runner import run_one
from tests._process_fake import FakeProcessRepo
from tests.test_process_triggers import FakeStore, FakeSts

NOW = dt.datetime(2026, 9, 15, tzinfo=dt.UTC)


def profiles(capacity):
    return parse_hardware_profiles(
        {
            "version": 1,
            "profiles": [
                {
                    "id": "standard", "label": "S", "description": "", "tier": "cpu",
                    "accelerator": None, "cpu": {"min": 0.25, "max": 4, "default": 1},
                    "memory_mb": {"min": 128, "max": 16384, "default": 512}, "gpu_count": None,
                    "max_queue_wait_seconds": 60, "image": None,
                    "backend": {"docker": {"device_requests": [], "capacity": capacity}},
                }
            ],
        }
    )


def queued(run_id="run-1"):
    return QueuedRun(
        id=run_id, process_id="proc-1", revision_id="rev-1", source_id=None, attempts=1,
        runtime={"kind": "inline_python"}, code="pass", env=[],
    )


async def _run(repo, executor, *, capacity):
    return await run_one(
        queued(), repo=repo, executor=executor, settings=Settings.from_env({}),
        storage_client=FakeStore(), resolve_secret=lambda ref: "x", now=NOW,
        sts_client=FakeSts(), profiles=profiles(capacity),
    )


@pytest.mark.asyncio
async def test_a_full_profile_releases_the_run_without_spending_an_attempt():
    repo = FakeProcessRepo(running_on_profile={"standard": 2})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(repo, executor, capacity=2)
    assert result.status == "queued"
    assert executor.launched == []
    assert repo.released == [
        {
            "run_id": "run-1",
            "next_attempt_at": NOW + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS),
            "detail": "profile 'standard' is at its capacity of 2 on this host",
        }
    ]
    assert repo.finished == []  # not a finish: the row goes back to queued


@pytest.mark.asyncio
async def test_a_profile_with_room_launches():
    repo = FakeProcessRepo(running_on_profile={"standard": 1})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(repo, executor, capacity=2)
    assert result.status == "succeeded"
    assert len(executor.launched) == 1
    assert repo.released == []


@pytest.mark.asyncio
async def test_an_unbounded_profile_never_counts():
    repo = FakeProcessRepo(running_on_profile={"standard": 99})
    executor = MemoryExecutor(results=[ExitStatus(0)])
    result = await _run(repo, executor, capacity=None)
    assert result.status == "succeeded"
    assert repo.counted == []  # the repo was not even asked


def test_release_phase_is_the_fixture_value():
    assert PHASE_PENDING_CAPACITY == "pending_capacity"
```
(Check that `FakeStore` and `FakeSts` are importable from `tests.test_process_triggers` — if the fakes live in `_process_fake.py` or another helper, import from there and say so in the report.)

Extend `FakeProcessRepo` in `_process_fake.py`:
```python
    #: K-3: what `count_running_on_profile` reports per profile id.
    running_on_profile: dict[str, int] = field(default_factory=dict)
    #: K-3: recorded capacity releases and the counts that were asked for.
    released: list[dict[str, Any]] = field(default_factory=list)
    counted: list[str] = field(default_factory=list)

    async def count_running_on_profile(self, profile_id: str, *, exclude_run_id: str) -> int:
        self.counted.append(profile_id)
        return self.running_on_profile.get(profile_id, 0)

    async def release_for_capacity(
        self, run_id: str, *, next_attempt_at: dt.datetime, detail: str
    ) -> None:
        self.released.append({"run_id": run_id, "next_attempt_at": next_attempt_at, "detail": detail})
        for row in self.enqueued:
            if row["run_id"] == run_id:
                row["status"] = "queued"
                row["phase"] = "pending_capacity"
```
and in `claim_run`, beside `row["status"] = "running"`, add `row["phase"] = "starting"`.

- [ ] **Step 5: Run to verify they fail**

Run: `uv run pytest tests/test_process_capacity.py -q`
Expected: FAIL — `TypeError: FakeProcessRepo.__init__() got an unexpected keyword argument 'running_on_profile'` before the fake is extended; after extending the fake, the first test fails because `run_one` launches (`result.status == "succeeded"`).

- [ ] **Step 6: The repo methods and statements**

`repo.py` — ABC, after `finish_run`:
```python
    @abc.abstractmethod
    async def count_running_on_profile(self, profile_id: str, *, exclude_run_id: str) -> int:
        """K-3: how many OTHER runs on this profile are `running` right now —
        the profile lives on the revision, so this joins process_revisions."""

    @abc.abstractmethod
    async def release_for_capacity(
        self, run_id: str, *, next_attempt_at: dt.datetime, detail: str
    ) -> None:
        """K-3: put a just-claimed run back to `queued` as `pending_capacity`
        without spending its attempt; the tick may claim it again at
        ``next_attempt_at``."""
```
`PgProcessRepo`:
```python
    async def count_running_on_profile(  # pragma: no cover
        self, profile_id: str, *, exclude_run_id: str
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM stac_higher.process_runs r"
                "  JOIN stac_higher.process_revisions rev ON rev.id = r.revision_id"
                " WHERE r.status = 'running' AND r.id <> %(run_id)s"
                "   AND coalesce(rev.runtime->'hardware'->>'profile', 'standard') = %(profile)s",
                {"run_id": exclude_run_id, "profile": profile_id},
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def release_for_capacity(  # pragma: no cover
        self, run_id: str, *, next_attempt_at: dt.datetime, detail: str
    ) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.process_runs"
                "   SET status = 'queued', started_at = NULL,"
                "       attempts = greatest(attempts - 1, 0),"
                "       phase = 'pending_capacity', phase_detail = %(detail)s,"
                "       next_attempt_at = %(at)s"
                " WHERE id = %(run_id)s AND status = 'running'",
                {"detail": detail, "at": next_attempt_at, "run_id": run_id},
            )
            await conn.commit()
```
Statements to amend in `PgProcessRepo` (exact edits):
- `claim_due_runs`: in the `due` CTE's WHERE, after the `queued` branch's deferral test add `AND (r.next_attempt_at IS NULL OR r.next_attempt_at <= %(now)s)` (inside the `queued` parenthesised branch); in the SET list add `phase = 'starting', phase_detail = NULL, next_attempt_at = NULL,`.
- `claim_run`: the same predicate and the same SET additions.
- `finish_run`: SET adds `phase = NULL, phase_detail = NULL,`.
- `reset_stalled_runs`: SET adds `phase = NULL, phase_detail = NULL,`.

`runner.py` — after the bounds check block and before `is_extract = …`:
```python
    # K-3 (spec §8): a profile's local capacity. The claim already flipped the
    # row to `running`; if the profile is full on this host the run goes back
    # to `queued` as *Waiting for capacity* with its attempt restored — the
    # same state the cluster leg shows while Kueue holds a Workload.
    backend = docker_backend(profile)
    if backend.capacity is not None:
        running = await repo.count_running_on_profile(profile.id, exclude_run_id=run.id)
        if running >= backend.capacity:
            detail = capacity_detail(profile.id, backend.capacity)
            await repo.release_for_capacity(
                run.id,
                next_attempt_at=at + dt.timedelta(seconds=CAPACITY_RETRY_SECONDS),
                detail=detail,
            )
            logger.info(
                "process run waiting for capacity",
                extra={"run_id": run.id, "process_id": run.process_id, "profile": profile.id, "running": running, "capacity": backend.capacity},
            )
            return RunResult(run.id, "queued", error=None)
```
with the imports `from pipeline.process.hardware import docker_backend` and `from pipeline.process.ledger import CAPACITY_RETRY_SECONDS, capacity_detail` (extend the existing import lines). `docker_backend` raising `HardwareProfileError` (malformed block) is a dead run — put the `docker_backend(profile)` call inside the existing bounds-check `try` so the same `except` handles it (the bounds try is the `HardwareProfileRejected` one after Task 5's split — read the current shape and place it in the DEAD path, not the infrastructure path).

- [ ] **Step 7: Run the tests and the gates**

Run: `uv run pytest tests/test_process_capacity.py tests/test_process_hardware.py tests/test_process_triggers.py -q` → PASS.
Run: `uv run pytest -q` and `uv run ruff check .` → green, pristine.

- [ ] **Step 8: Commit**

```bash
git add services/pipeline/src/pipeline/process/hardware.py services/pipeline/src/pipeline/process/ledger.py services/pipeline/src/pipeline/process/repo.py services/pipeline/src/pipeline/process/runner.py services/pipeline/tests/_process_fake.py services/pipeline/tests/test_process_hardware.py services/pipeline/tests/test_process_capacity.py
git commit -m "feat(process): per-profile capacity — a full profile releases the claimed run as pending_capacity (15 s, no attempt spent); phase on claim/finish/stall; docker backend block parsed (K-3)"
```

### Task 3: `DockerExecutor` — `NanoCpus`, `DeviceRequests`, the NVIDIA runtime probe

**Files:**
- Modify: `services/pipeline/src/pipeline/process/executor.py` (append `HardwareUnavailable`)
- Modify: `services/pipeline/src/pipeline/process/docker_executor.py` (`launch`, a `gpu_runtime_available()` probe)
- Modify: `services/pipeline/src/pipeline/process/runner.py` (catch `HardwareUnavailable` → dead)
- Modify: `docker-compose.yml` (`docker-socket-proxy`: `- INFO=1`)
- Test: `services/pipeline/tests/test_process_executor.py` (append), `services/pipeline/tests/test_process_capacity.py` (append one run_one case)

**Interfaces:**
- Consumes: `RunSpec.cpu`, `RunSpec.gpu_count`, `RunSpec.profile` (K-1), `docker_backend` (Task 2), `FakeApi` (the file's Engine-API double: `FakeApi({"create": {...}, "info": {...}})` matches by substring of the path).
- Produces:
  ```python
  # executor.py
  class HardwareUnavailable(ExecutorError):
      """The run asks for hardware this backend/host cannot provide — configuration, not a fault: a dead run, never a requeue."""
  # docker_executor.py
  def gpu_runtime_available(self) -> bool     # GET /info once, cached; True iff "nvidia" in Runtimes
  ```

- [ ] **Step 1: Failing executor tests**

Append to `tests/test_process_executor.py` (reuse the file's `FakeApi`, `executor_with`, `settings()`, `RUN`, `PROC`, `RunCredentials`, `run_staging_prefix` helpers — read the first launch test):
```python
def _spec(**overrides):
    from pipeline.process.hardware import parse_hardware_profiles

    gpu_profile = parse_hardware_profiles(
        {
            "version": 1,
            "profiles": [
                {
                    "id": "standard", "label": "S", "description": "", "tier": "cpu",
                    "accelerator": None, "cpu": {"min": 0.25, "max": 4, "default": 1},
                    "memory_mb": {"min": 128, "max": 16384, "default": 512}, "gpu_count": None,
                    "max_queue_wait_seconds": 60, "image": None, "backend": {"docker": {"capacity": 2}},
                },
                {
                    "id": "gpu-l4", "label": "L4", "description": "", "tier": "gpu",
                    "accelerator": {"vendor": "NVIDIA", "model": "L4", "memory_gb": 24},
                    "cpu": {"min": 2, "max": 8, "default": 4},
                    "memory_mb": {"min": 8192, "max": 32768, "default": 16384},
                    "gpu_count": {"min": 1, "max": 2, "default": 1},
                    "max_queue_wait_seconds": 7200, "image": "cuda",
                    "backend": {"docker": {"device_requests": [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}], "capacity": 1}},
                },
            ],
        }
    )
    base = dict(
        run_id=RUN, process_id=PROC, image="img", env={}, memory_mb=256, timeout_seconds=60,
        cpu=1.0, gpu_count=0, profile=gpu_profile.standard,
    )
    base.update(overrides)
    if "profile" in overrides and isinstance(overrides["profile"], str):
        base["profile"] = gpu_profile.get(overrides["profile"])
    return RunSpec(**base)


def test_launch_sets_nanocpus_from_cpu():
    api = FakeApi({"create": {"Id": "c1"}})
    executor_with(api).launch(_spec(cpu=1.5))
    assert api.created_config()["HostConfig"]["NanoCpus"] == 1_500_000_000
    assert "DeviceRequests" not in api.created_config()["HostConfig"]
    assert not any("info" in p for _, p, _ in api.calls)  # no GPU asked, no probe


def test_launch_sends_device_requests_when_the_host_has_an_nvidia_runtime():
    api = FakeApi({"create": {"Id": "c1"}, "info": {"Runtimes": {"nvidia": {}, "runc": {}}}})
    executor_with(api).launch(_spec(profile="gpu-l4", cpu=4, gpu_count=2))
    host = api.created_config()["HostConfig"]
    assert host["DeviceRequests"] == [{"Driver": "nvidia", "Count": 2, "Capabilities": [["gpu"]]}]


def test_launch_refuses_gpus_on_a_host_without_the_runtime():
    from pipeline.process.executor import HardwareUnavailable

    api = FakeApi({"create": {"Id": "c1"}, "info": {"Runtimes": {"runc": {}}}})
    with pytest.raises(HardwareUnavailable, match="profile 'gpu-l4' needs an NVIDIA container runtime"):
        executor_with(api).launch(_spec(profile="gpu-l4", cpu=4, gpu_count=1))
    assert not any("create" in p for _, p, _ in api.calls)  # nothing was created


def test_the_runtime_probe_is_cached_per_executor():
    api = FakeApi({"create": {"Id": "c1"}, "info": {"Runtimes": {"nvidia": {}}}})
    ex = executor_with(api)
    ex.launch(_spec(profile="gpu-l4", cpu=4, gpu_count=1))
    ex.launch(_spec(profile="gpu-l4", cpu=4, gpu_count=1))
    assert sum(1 for _, p, _ in api.calls if "info" in p) == 1


def test_an_unreachable_info_endpoint_is_an_executor_fault_not_a_dead_run():
    api = FakeApi({"info": ExecutorUnavailable("docker GET /info failed: 403")})
    with pytest.raises(ExecutorUnavailable):
        executor_with(api).launch(_spec(profile="gpu-l4", cpu=4, gpu_count=1))
```
Append to `tests/test_process_capacity.py`:
```python
@pytest.mark.asyncio
async def test_hardware_unavailable_is_a_dead_run():
    from pipeline.process.executor import HardwareUnavailable

    class RefusingExecutor(MemoryExecutor):
        def launch(self, spec):
            raise HardwareUnavailable("profile 'gpu-l4' needs an NVIDIA container runtime this Docker host does not expose")

    repo = FakeProcessRepo()
    result = await _run(repo, RefusingExecutor(results=[]), capacity=None)
    assert result.status == "dead"
    assert repo.finished[-1]["status"] == "dead"
    assert "NVIDIA container runtime" in repo.finished[-1]["error"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_process_executor.py -q -k "nanocpus or device_requests or runtime or info"` and `uv run pytest tests/test_process_capacity.py -q`
Expected: FAIL — no `NanoCpus`, no `HardwareUnavailable`.

- [ ] **Step 3: Implement**

`executor.py`, after `RunTimeout`:
```python
class HardwareUnavailable(ExecutorError):
    """The run asks for hardware this backend or host cannot provide (K-3):
    configuration, not a fault — the run dies naming it, never requeues."""
```
`docker_executor.py`:
- imports: `from pipeline.process.executor import (..., HardwareUnavailable, ...)`, `from pipeline.process.hardware import docker_backend`.
- a field on the dataclass: `_gpu_runtime: bool | None = field(default=None, init=False, repr=False)` (`from dataclasses import dataclass, field`).
- the probe:
```python
    def gpu_runtime_available(self) -> bool:
        """Does this daemon expose an NVIDIA container runtime? `GET /info`
        once per executor (the socket proxy allows it with INFO=1); Docker
        Desktop on macOS never does, a Linux host with the toolkit does."""
        if self._gpu_runtime is None:
            info = self._request("GET", "/info")
            runtimes = info.get("Runtimes") if isinstance(info, dict) else None
            self._gpu_runtime = isinstance(runtimes, dict) and "nvidia" in runtimes
        return self._gpu_runtime
```
- in `launch`, before the `config = {...}` literal:
```python
        # K-3 (spec §8): the profile's numbers reach the daemon. Memory was
        # already a limit; cpu becomes one; GPUs are device requests, and only
        # on a host that can honour them — otherwise the run dies here, before
        # a container exists, naming the profile.
        device_requests: list[dict] | None = None
        if spec.gpu_count > 0:
            profile_id = spec.profile.id if spec.profile else "unknown"
            if not self.gpu_runtime_available():
                raise HardwareUnavailable(
                    f"profile {profile_id!r} needs an NVIDIA container runtime this Docker host does not expose"
                )
            requests = docker_backend(spec.profile).device_requests if spec.profile else ()
            device_requests = [{**r, "Count": spec.gpu_count} for r in requests]
```
  and in `HostConfig`, after `"Memory"`: `"NanoCpus": int(round(spec.cpu * 1_000_000_000)),`; after the literal: `if device_requests: config["HostConfig"]["DeviceRequests"] = device_requests`.
- `runner.py`: extend the `execute_run` try's excepts with, BEFORE the infrastructure branch:
```python
    except HardwareUnavailable as err:
        # The host cannot provide what the profile promises — configuration,
        # like an unknown profile: dead, naming it, no retry.
        await _finish(repo, run, "dead", None, str(err), None, at, on_dead=on_dead)
        return RunResult(run.id, "dead", error=str(err))
```
  (`HardwareUnavailable` is not an `ExecutorUnavailable`, so ordering only matters for readability; import it beside `ExecutorUnavailable`.)
- `docker-compose.yml`, `docker-socket-proxy` environment, after `- POST=1`:
```yaml
      # K-3: GET /info is how the executor learns whether the daemon exposes
      # an NVIDIA runtime before sending DeviceRequests. Read-only.
      - INFO=1
```
- Update `test_launch_sets_limits_network_and_never_mounts` only if it asserts the exact `HostConfig` key set (it asserts individual keys — leave it).

- [ ] **Step 4: Run the tests and the gates**

Run: `uv run pytest tests/test_process_executor.py tests/test_process_capacity.py tests/test_process_triggers.py -q` → PASS.
Run: `uv run pytest -q` and `uv run ruff check .` → green, pristine.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/process/executor.py services/pipeline/src/pipeline/process/docker_executor.py services/pipeline/src/pipeline/process/runner.py docker-compose.yml services/pipeline/tests/test_process_executor.py services/pipeline/tests/test_process_capacity.py
git commit -m "feat(process): DockerExecutor honours the profile — NanoCpus from cpu, DeviceRequests behind an NVIDIA-runtime probe, HardwareUnavailable is a dead run; socket proxy INFO=1 (K-3)"
```

### Task 4: The phase chip, docs

**Files:**
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` — `RunRow`
- Modify: `docs/processes.md` — the `## When does a run start?` section; `services/pipeline/README.md` — the `## Hardware profiles (K-1)` section (one paragraph); `docs/backend.md` — the socket-proxy scope line if it lists the proxy's flags
- Test: `app/src/__tests__/process-run-row-hardware.test.tsx` (extend — K-2 created it and exported `RunsCard`)

**Interfaces:**
- Consumes: `ApiProcessRun.phase` / `phase_detail` (Task 1); `RunsCard`'s existing props and the test file's `RUNS` / `REVISIONS` / mocks.
- Produces: in `RunRow`, after the `rate limited` badge:
  ```tsx
  {run.status === "queued" && run.phase === "pending_capacity" && (
    <Badge variant="outline" title={run.phase_detail ?? undefined}>Waiting for capacity</Badge>
  )}
  {run.status === "running" && run.phase === "starting" && (
    <Badge variant="outline">Starting</Badge>
  )}
  ```

- [ ] **Step 1: Failing test**

Extend `app/src/__tests__/process-run-row-hardware.test.tsx`: give `baseRun` the fields `phase: null, phase_detail: null`, add two runs
```ts
  { ...baseRun, id: "run-4", revision_id: "rev-new", status: "queued" as const, phase: "pending_capacity" as const, phase_detail: "profile 'standard' is at its capacity of 2 on this host" },
  { ...baseRun, id: "run-5", revision_id: "rev-new", status: "running" as const, phase: "starting" as const },
```
and the case:
```tsx
  it("shows the K-3 phase chip with the detail as its tooltip", () => {
    render(<RunsCard id="p1" canMutate={false} isExtractor={false} revisions={REVISIONS as never} />);
    const waiting = screen.getByText("Waiting for capacity");
    expect(waiting).toHaveAttribute("title", "profile 'standard' is at its capacity of 2 on this host");
    expect(screen.getByText("Starting")).toBeInTheDocument();
    expect(screen.queryAllByText("Waiting for capacity")).toHaveLength(1);
  });
```
The existing summary assertion in that file lists the `run-hardware` cells in order — extend its expected array for the two new runs (both pin `rev-new`: `"2 vCPU · 2 GB · waits up to 30 min"` twice more at the end).

- [ ] **Step 2: Run to verify it fails** — `npx vitest run src/__tests__/process-run-row-hardware.test.tsx` → FAIL (no chip).

- [ ] **Step 3: Implement the chip** (the snippet above, in `RunRow`'s badge row; `Badge` is already imported).

- [ ] **Step 4: Docs**

`docs/processes.md`, in `## When does a run start?`, append:
```markdown
A claimed run can also wait for **capacity**: each hardware profile carries
how many runs of it one Docker host runs at once (the profile's
`backend.docker.capacity`). When that many are already running, the run goes
back to the queue as *Waiting for capacity* — the tooltip names the profile
and the limit — and is tried again every 15 s without spending an attempt.
On a cluster the same wait is Kueue holding the Workload for quota. A GPU
profile on a host without an NVIDIA container runtime does not wait: the run
dies naming the profile, because no amount of waiting provides the hardware.
```
`services/pipeline/README.md`, in `## Hardware profiles (K-1)`, replace the sentence `\`backend\` is opaque until K-3 (…) and K-5 (Kubernetes) consume it.` with:
```markdown
K-3 consumes `backend.docker`: `NanoCpus` from the run's `cpu`,
`DeviceRequests` (each with `Count` = the run's `gpu_count`) when `GET /info`
on the socket proxy (`INFO=1`) reports an `nvidia` runtime — otherwise a GPU
run dies naming the profile — and `capacity`, the per-host concurrent-run
limit per profile: a claimed run on a full profile is released back to
`queued` as `phase = pending_capacity` with `next_attempt_at = now + 15 s`
and its attempt restored. `backend.kubernetes` stays opaque until K-5.
```
`docs/backend.md`: if the socket proxy's allowed endpoints are listed, add `INFO` (read-only `GET /info`, K-3).

- [ ] **Step 5: Gates and commit**

Run: `npm run verify` from the worktree root → green.
```bash
git add app/src/components/processes/ProcessDetailPage.tsx app/src/__tests__/process-run-row-hardware.test.tsx docs/processes.md services/pipeline/README.md docs/backend.md
git commit -m "feat(processes): Waiting-for-capacity / Starting phase chips on run rows; docs for per-profile capacity (K-3)"
```

### Task 5: Verify, merge, migrate, live check (lead only)

- [ ] Worktree: `npm run verify`, pytest + ruff. `git checkout ai/main && git merge ai/k3-docker-profile --no-ff`; all three gates on `ai/main`.
- [ ] `docker compose up -d --build pipeline docker-socket-proxy`; migration 029 applies on the app's first request (start the dev server or hit any API route); `psql` shows the seven columns and the CHECK.
- [ ] Live: the pipeline log shows no `HardwareProfileError`; a test run on `standard` succeeds with `phase` cycling `starting → NULL` (`SELECT status, phase, phase_detail FROM stac_higher.process_runs ORDER BY created_at DESC LIMIT 3`). Capacity: only observable once M3-D's concurrency is deployed (two `process_run_now` jobs in flight) — with `cpu-large` (capacity 1) and two simultaneous test runs the second row shows `queued / pending_capacity` and the UI chip; if M3-D is not yet on `ai/main`, record that the capacity check is unit-tested only and re-verify at K-4 or after M3-D deploys.
- [ ] e2e: `processes` spec, then the whole suite. Chrome: a run row with the chip (screenshot noted).
- [ ] `TODO.md` tick + landed note (the `next_attempt_at` ruling, the `INFO=1` widening, what was live-checked); `docs/FEATURES.md`; `docs/ISSUES.md` for gaps; worktree removal.

## Self-review

- Spec §5.2: the six columns (T1) + the ruled `next_attempt_at`; `cancelled` deferred to K-4 per the TODO slice text — noted in the constraints. §8: `NanoCpus`, `DeviceRequests` behind the runtime probe, capacity → `pending_capacity` + 15 s + no attempt (T2/T3). §3.4 docker block read (T2). §9 phase chip (T4); Cancel/`cancelled`/TestRunCard phases are K-4.
- Type consistency: `DockerBackend.capacity: int | None` / `docker_backend(profile)` (T2) used by T3's launch; `count_running_on_profile(profile_id, *, exclude_run_id)` / `release_for_capacity(run_id, *, next_attempt_at, detail)` identical in ABC, Pg, fake and `run_one`; `capacity_detail` text equals the string T4's test asserts and T2's test expects; `PHASES` order equals the fixture and the migration CHECK (T1 test compares).
- Placeholders: none — each step carries its code and command.
