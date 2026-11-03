# Z-8a · Sink API readiness for the UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** #129 (queue Z, epic #83; blocks Z-8 #94) · **Branch:** `feat/z8a-sink-api-readiness` (worktree `.claude/worktrees/z8a-sink-api-readiness`, off `main` at c2d8adc)

**Goal:** Make the cube sink API ready for Z-8's sink card, so Z-8 stays UI-only. That means seven things: a typed and fixture-pinned `last_maintenance`, a ledger vocabulary that matches what the pipeline writes, one derived health state, a source-picker list endpoint, `recent` ordered by activity, an optimistic PATCH, and a separate code for a non-S3 reference source.

**Architecture:** Two new contracts live in `tests/contract-fixtures/`:
- `cube-maintenance-summary.json` (new). The pipeline is the only writer of this shape and has no reader for it, so pytest pins the *producer*: it drives the real `run_cube_maintain` / `run_cube_measure` through the existing `Cube` harness and checks each output against a named document. Vitest pins the *reader*: a lenient Zod `cubeMaintenanceSchema` must parse every document back to itself.
- `cube-append-status.json` (changed). `duplicate` moves to a new `appended_reasons` list, and a new `failed_reasons` list holds `crash_loop`.

Everything else is app-side. Pure vocabularies and derivations go in `app/src/lib/cubes/status.ts`: labels, `cubeSinkHealth` and `referenceSourceRefusal`. SQL goes in `storage.ts`. The route changes are in `cube-sink.ts`, plus one new read-only route, `GET /api/associations/reference-sources`.

**Tech Stack:** TypeScript, Astro 7 server routes, Zod 4, Vitest; Python 3.12, pytest + pytest-asyncio, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §3 (contracts), §7 (API), §9 (UI: the sink card's needs). The `last_maintenance` reference is `docs/monitoring.md` "Cube repositories". Z-8's consumer facts are in `gh issue view 94` ("Facts…" section).

## Global Constraints

- Work only in the worktree `.claude/worktrees/z8a-sink-api-readiness`, on branch `feat/z8a-sink-api-readiness`.
- **Teammate constraint:** run `npm run verify` (repo root) and `uv run pytest` + `uv run ruff check .` (from `services/pipeline/`) **only**. No e2e, no dev server, no Docker, no push.
- **No migration.** `cube_appends.reason` has no CHECK (migration 032 constrains only `status`), `last_maintenance` is `jsonb`, and the `recent` re-order needs no index (Decision 13).
- **No new dependencies**, in either runtime.
- Both runtimes test the new fixture: `app/src/__tests__/contract-fixtures.test.ts` (vitest) and `services/pipeline/tests/test_contract_fixtures.py` + `tests/test_cube_maintain.py` (pytest).
- The pipeline's maintenance code (`maintain.py`) does not change. Only `pipeline/cubes/config.py`'s vocabulary tuples change (Decision 5).
- 422/409 refusals keep the `{ error, code }` JSON shape. Authz failures use `authzError` (401 `unauthenticated`).
- New codes, verbatim: **`unsupported_source_protocol`** (422), and **`cube_sink_conflict`** (409, now also on PATCH).
- `docs/backend.md` gets a row for the new endpoint and the new codes, and `tests/contract-fixtures/README.md` lists the new fixture.
- Docs carry no status and link no issue (AGENTS.md link rule).
- Do not touch `components/ui/`. There is no UI in this slice.

## Decisions this plan takes (flag in the PR)

1. **Route path: `GET /api/associations/reference-sources`**, not `GET /api/associations?direction=ingest&storage_mode=reference`. Why: it has one purpose. A generic filtered list would suggest filters it doesn't support and would naturally use association visibility, not PUT's rule (Decision 3). The file goes in the issue's `app/src/pages/api/associations/` directory.
2. **Response: one row per source collection.** The shape is `{ collections: [{ collection_id, title, associations: [{ association_id, connection_id, protocol, anonymous }], refusal }] }`. `refusal` is the 422 code PUT would return for that source, or `null`. Why: PUT judges a source by *all* of its reference associations. `referenceSourceRefusal()` computes the per-collection verdict, and the route and the list share it, so the picker's pre-filter cannot disagree with PUT.
3. **Group rule = `canManageCollection`.** A collection is listed when its `collection_settings.group_id` is NULL (unowned, ADR 0003) or is one of the caller's groups. Admins see all. This is not the connection-group rule `/api/monitoring/flows` uses. Why: PUT refuses any source the caller can't manage (`source_collection_not_found`), so listing one would offer a dead choice.
4. **`permissions.ts` does not change.** Why: the route is a GET. `matchGatedRoute` returns null for every GET, and there is no audit. Member+ means any authenticated caller (the `flows.ts` precedent), and the group filter is applied inside the SQL. A test pins that the path is ungated.
5. **Ledger reasons.**
   - `skipped` reasons stay a closed set, minus `duplicate`.
   - `appended` carries `null` or `duplicate`.
   - `failed` is a closed set `["crash_loop"]` **plus free text** (an exception message, at most `MAX_ERROR_CHARS` = 500).
   - The UI labels a known code and shows any other string verbatim.

   Why: the free text is the operator's only diagnostic, so it can't be closed. `pipeline/cubes/config.py` is edited even though the issue's file list omits it, because pytest pins `SKIP_REASONS` to the fixture.
6. **Fixture style `producer-shapes`** (new, documented in the README). It holds the vocab lists, named `documents` and app-only `cases[]`. pytest checks that each named scenario produces a summary with the same key tree, the same JSON types and the same enum values as its document. Why: the pipeline has no reader to run accept/reject cases through, so pinning what it *writes* is the cross-runtime check that can fail.
7. **The app reader is lenient where a newer writer could grow.**
   - Accepted: unknown keys (stripped), unknown `attention` reasons, unknown `publish_outcome`, unknown size kinds.
   - Rejected: a missing or unknown `status`, and wrong container or scalar types.
   - Absent and `null` stay distinct: absent means the run never got there, and `null` means it got there with nothing to report.
   - An unreadable stored summary becomes `last_maintenance: null` with a `console.warn`, never a 500.

   Why: this is the `push-upload-status` direction (strict writer, lenient reader).
8. **Health states, first match wins:** `disabled` > `error` > `attention` > `stale` > `waiting` > `ok`.
   - `disabled`: `!enabled`, even when broken. It is the operator's choice, and `last_error` still shows in `reasons`.
   - `error`: `last_error` set (a transport outage, an invalid config or a deleted source), then `last_maintenance.status === "failed"`.
   - `attention`: the maintenance attention reasons, plus `failed_rows` when a failed row is among `recent`.
   - `stale`: Decision 9.
   - `waiting`: nothing appended yet.
   - `ok`.

   Why: a card shows the most actionable state.
9. **Staleness:** a sink is `stale` iff `config.window.max_age` is set, `last_appended_at` is set and `now − last_appended_at > max_age`. Why: at that point every step has aged out of the window, so the next maintenance trim empties the cube, which is the "source stopped" signal. A sink with only `max_steps`, or no window, is **never** stale, because nothing in the row knows the source's cadence. The card shows `last_appended_at` for those.
10. **`failed_rows` reads `recent`, not `counts.failed`.** Why: `counts` covers 7 days (the prune), so one old failure would pin the card to `attention` for a week. `recent` is the last 20 rows by activity (Decision 13).
11. **PATCH lock: the server reads the version, as PUT does (#98).** The client sends no `If-Match`. Each attempt re-reads the row, re-runs the source checks on *that* row (when enabling), and writes `WHERE updated_at = version`. There is one retry, then 409 `cube_sink_conflict`. There is no `hasRepository` guard, because `enabled` is not layout. Why: the race that matters is check-then-write. A collection delete disabling the sink (`source_collection_deleted`) between the check and the write must not be re-enabled unchecked.
12. **A non-S3 reference source is refused, not filtered out**, with 422 `unsupported_source_protocol`. The order is `no_reference_ingest` → `unsupported_source_protocol` → `signed_source_unsupported`. `anonymous` is now `false` for any non-s3 connection. Why: filtering would let a mixed source through whose non-s3 rows can never resolve, or would misreport `no_reference_ingest`. Note: the association routes have refused `storage_mode: reference` on non-s3 connections since Phase 4, so this only defends against hand-written or legacy rows.
13. **`recent` order is `updated_at DESC, id DESC`, with no index and no migration.** Why: `cube_appends.updated_at` is bumped on every take and outcome, and the ledger is pruned to 7 days per sink, so a top-20 sort is cheap.
14. **`cubeSinkHealth` is a pure function. GET gains no `health` field.** Why: YAGNI. Z-8's card imports the function and re-evaluates it against its own clock.
15. **Spec §3.2 gets an *As built (Z-8a)* note** for the reason lists, following #131's precedent for as-built notes.

## Review Focus

1. **Re-enabling while the source collection is being deleted.** The delete hook disables the sink with `last_error = source_collection_deleted`. A PATCH `{enabled:true}` that checked earlier must lose, re-read and refuse. Task 5: `a source deleted mid-request: the re-check on the fresh row refuses`.
2. **A summary from a failed run, or a run that found no repository.** Those keys are *absent*, not null. The type and the health function must not assume `gc`, `sizes` or `repository` exist. Tasks 2–4: documents `failed` and `no_repository`, `health: a failed maintenance run is error`, and `an unreadable stored summary is null, never a 500`.
3. **A source whose reference ingest sits on an sftp connection** (a legacy row). It must give the new code on both PUT and enable, and must not read as "signed". Task 6: `refuses a reference ingest on a non-s3 connection with its own code`.
4. **A step that just failed with an old `item_datetime`** must be in `recent`, so the card shows it. Task 5: `orders recent ledger rows by activity`.
5. **The picker must not show a foreign group's collection, or one PUT would refuse.** Task 7: `filters by the caller's groups, keeping unowned collections`, and `marks each collection with the refusal PUT would give`.

---

## Before Task 1

From the worktree root:

```bash
npm install
(cd services/pipeline && uv sync --extra dev)
```

Vitest commands below run from `app/` (`npx vitest run <file>`). `uv run …` commands run from `services/pipeline/`. The PostToolUse hook runs `astro check` after each `.ts` edit; fix any errors it reports before going on.

---

### Task 1: Ledger vocabulary matches what the pipeline writes

**Files:**
- Modify: `tests/contract-fixtures/cube-append-status.json`
- Modify: `services/pipeline/src/pipeline/cubes/config.py:17-26`
- Modify: `app/src/lib/cubes/status.ts`
- Test: `services/pipeline/tests/test_contract_fixtures.py` (`test_cube_append_vocabulary_matches_golden`), `app/src/__tests__/contract-fixtures.test.ts` (the `cube append status vocabulary` describe), `app/src/__tests__/cubes-status.test.ts` (create)

**Interfaces:**
- Produces:
  - Python `pipeline.cubes.config`: `SKIP_REASONS = ("late", "no_source_connection", "unsupported_layout", "source_missing", "no_datetime")`, `APPENDED_REASONS = ("duplicate",)` and `FAILED_REASONS = ("crash_loop",)`.
  - TS `status.ts`: `CUBE_SKIP_REASONS` (the same five), `CUBE_APPENDED_REASONS = ["duplicate"] as const`, `CUBE_FAILED_REASONS = ["crash_loop"] as const`, the types `CubeAppendedReason` and `CubeFailedReason`, and the maps `CUBE_APPENDED_REASON_LABEL` and `CUBE_FAILED_REASON_LABEL`.
  - `cubeLedgerReasonLabel(status: CubeAppendStatus, reason: string | null): string | null`.

- [ ] **Step 1: Update the fixture.** Replace `cube-append-status.json` with:

```json
{
  "$comment": "Z-2, Z-8a (virtual cube spec §3.2, §5.2): the stac_higher.cube_appends status vocabulary and its reasons, as the pipeline writes them. pending rows carry null. skipped rows carry a skip reason (closed). appended rows carry null or an appended reason: `duplicate` means the step was already in the cube (write.py), a no-op that counts as appended. failed rows carry a failed reason (closed: `crash_loop`, append.py) OR free error text of at most failed_reason_max_chars (write.py MAX_ERROR_CHARS); readers show unknown failed text verbatim. `no_datetime` is written by the dispatcher (§5.2). Consumed by app/src/lib/cubes/status.ts (whose label maps must cover every closed reason), pipeline/cubes/config.py, and migration 032's CHECK constraint (cubes-migration.test.ts, statuses only).",
  "style": "pinned-enum",
  "statuses": ["pending", "appended", "skipped", "failed"],
  "terminal": ["appended", "skipped", "failed"],
  "skip_reasons": ["late", "no_source_connection", "unsupported_layout", "source_missing", "no_datetime"],
  "appended_reasons": ["duplicate"],
  "failed_reasons": ["crash_loop"],
  "failed_reason_max_chars": 500
}
```

- [ ] **Step 2: Write the failing tests**

pytest: replace `test_cube_append_vocabulary_matches_golden` in `tests/test_contract_fixtures.py`. Add `APPENDED_REASONS` and `FAILED_REASONS` to its `pipeline.cubes.config` import.

```python
def test_cube_append_vocabulary_matches_golden():
    from pipeline.cubes.append import REASON_CRASH_LOOP
    from pipeline.cubes.repo import REASON_NO_DATETIME
    from pipeline.cubes.write import (
        MAX_ERROR_CHARS,
        REASON_DUPLICATE,
        REASON_LATE,
        REASON_UNSUPPORTED_LAYOUT,
    )

    assert list(APPEND_STATUSES) == CUBE_APPEND_STATUS["statuses"]
    assert list(TERMINAL_STATUSES) == CUBE_APPEND_STATUS["terminal"]
    assert list(SKIP_REASONS) == CUBE_APPEND_STATUS["skip_reasons"]
    assert list(APPENDED_REASONS) == CUBE_APPEND_STATUS["appended_reasons"]
    assert list(FAILED_REASONS) == CUBE_APPEND_STATUS["failed_reasons"]
    assert MAX_ERROR_CHARS == CUBE_APPEND_STATUS["failed_reason_max_chars"]
    # The writers' constants sit in the right list (duplicate is written on appended rows).
    assert {REASON_LATE, REASON_UNSUPPORTED_LAYOUT, REASON_NO_DATETIME} <= set(SKIP_REASONS)
    assert REASON_DUPLICATE in APPENDED_REASONS and REASON_DUPLICATE not in SKIP_REASONS
    assert REASON_CRASH_LOOP in FAILED_REASONS
```

vitest, in the `contract-fixtures.test.ts` `cube append status vocabulary` describe: widen the fixture type with `appended_reasons` and `failed_reasons`, then extend the two tests. Import the new constants and labels from `@/lib/cubes/status`.

```ts
  it("pins the statuses, terminal set and every closed reason list", () => {
    expect([...CUBE_APPEND_STATUSES]).toEqual(fixture.statuses);
    expect([...CUBE_APPEND_TERMINAL]).toEqual(fixture.terminal);
    expect([...CUBE_SKIP_REASONS]).toEqual(fixture.skip_reasons);
    expect([...CUBE_APPENDED_REASONS]).toEqual(fixture.appended_reasons);
    expect([...CUBE_FAILED_REASONS]).toEqual(fixture.failed_reasons);
  });

  it("labels every status and every closed reason", () => {
    expect(Object.keys(CUBE_APPEND_STATUS_LABEL).sort()).toEqual([...fixture.statuses].sort());
    expect(Object.keys(CUBE_SKIP_REASON_LABEL).sort()).toEqual([...fixture.skip_reasons].sort());
    expect(Object.keys(CUBE_APPENDED_REASON_LABEL).sort()).toEqual([...fixture.appended_reasons].sort());
    expect(Object.keys(CUBE_FAILED_REASON_LABEL).sort()).toEqual([...fixture.failed_reasons].sort());
  });
```

Create `app/src/__tests__/cubes-status.test.ts` (`// @vitest-environment node`), with a `describe("cubeLedgerReasonLabel")` holding:
- `labels a duplicate on an appended row`: `cubeLedgerReasonLabel("appended", "duplicate")` equals `CUBE_APPENDED_REASON_LABEL.duplicate`.
- `labels crash_loop and shows other failed text verbatim`: `cubeLedgerReasonLabel("failed", "crash_loop")` equals `CUBE_FAILED_REASON_LABEL.crash_loop`. `cubeLedgerReasonLabel("failed", "OSError: [Errno 13] denied")` equals `"OSError: [Errno 13] denied"`.
- `labels a skip reason and passes an unknown one through`: `("skipped", "late")` gives the label; `("skipped", "from_the_future")` gives `"from_the_future"`.
- `null reason is null`: `cubeLedgerReasonLabel("appended", null)` is `null`.
- `a reason under the wrong status is not relabelled`: `cubeLedgerReasonLabel("skipped", "duplicate")` is `"duplicate"`.

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest -q tests/test_contract_fixtures.py -k cube_append` → FAIL (`ImportError: APPENDED_REASONS`).
Run: `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/cubes-status.test.ts` → FAIL (missing exports).

- [ ] **Step 4: Implement**
- `config.py`: drop `"duplicate"` from `SKIP_REASONS`. Add `APPENDED_REASONS` and `FAILED_REASONS`, each with a one-line `#:` comment ("the closed part; any other failed reason is error text").
- `status.ts`:
  - Mirror the three lists.
  - Move the `duplicate` label to `CUBE_APPENDED_REASON_LABEL` (`"Already in the cube"`).
  - Add `CUBE_FAILED_REASON_LABEL = { crash_loop: "Gave up after repeated attempts" }`.
  - `cubeLedgerReasonLabel` looks the reason up only in its own status's map and otherwise returns the reason itself.

- [ ] **Step 5: Run to verify they pass**

Run: `uv run pytest -q tests/test_contract_fixtures.py tests/test_cube_append.py` → PASS.
Run: `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/cubes-status.test.ts src/__tests__/cubes-migration.test.ts` → PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/contract-fixtures/cube-append-status.json services/pipeline/src/pipeline/cubes/config.py services/pipeline/tests/test_contract_fixtures.py app/src/lib/cubes/status.ts app/src/__tests__/contract-fixtures.test.ts app/src/__tests__/cubes-status.test.ts
git commit -m "Z-8a: ledger reasons as the pipeline writes them — duplicate is appended, failed is crash_loop or text"
```

---

### Task 2: `cube-maintenance-summary.json`, the pipeline half

**Files:**
- Create: `tests/contract-fixtures/cube-maintenance-summary.json`
- Test: `services/pipeline/tests/test_contract_fixtures.py` (vocab pin), `services/pipeline/tests/test_cube_maintain.py` (producer scenarios, appended at the end)

**Interfaces:**
- Consumes: `pipeline.cubes.maintain` (`STATUS_*`, `ATTENTION_*`, `MEASURE_REASONS`, `KINDS`, `OTHER_KIND`, `GC_COUNTS`, `PUBLISH_ERROR`, `run_cube_maintain`, `run_cube_measure`); `pipeline.cubes.collection` (`PUBLISHED`, `UNCHANGED`, `SUPERSEDED`, `MISSING_COLLECTION`); the `Cube` harness already in `test_cube_maintain.py`.
- Produces: the fixture keys Task 3 reads: `statuses`, `attention`, `measure_reasons`, `publish_outcomes`, `size_kinds`, `gc_counts`, `duration_phases`, `documents` (named `windowed`, `windowless`, `no_repository`, `failed`, `publish_failed`, `measure_failed`) and `cases[]` (`{ name, base, patch?, remove?, app }`).

- [ ] **Step 1: Write the fixture.** The vocabulary is exact:

```json
{
  "$comment": "Z-8a: stac_higher.cube_sinks.last_maintenance (docs/monitoring.md 'Cube repositories'; virtual cube spec §10). Writer: pipeline/cubes/maintain.py run_cube_maintain + run_cube_measure (the readout merge). It is the STRICT side and has no reader, so pytest (tests/test_cube_maintain.py) drives each named scenario through the real code and asserts the produced summary has the same key tree, JSON types and enum values as its document. Reader: app/src/lib/cubes/schemas.ts cubeMaintenanceSchema (LENIENT: unknown keys stripped, unknown attention reasons / publish outcomes / size kinds kept; strict on status and on types). Keys are ABSENT when the run never reached them (no repository, failed early, readout not landed yet); null only where the doc says so (expired_snapshots/gc without a window, publish_outcome when nothing was handed to the writer).",
  "style": "producer-shapes",
  "statuses": ["ok", "attention", "failed"],
  "attention": ["repo_size", "gc_delete_failures", "unrecorded_tip", "publish_failed", "expiry_skipped", "measure_failed"],
  "measure_reasons": ["repo_size", "measure_failed"],
  "publish_outcomes": ["published", "unchanged", "superseded", "missing_collection", "error"],
  "size_kinds": ["transactions", "overwritten", "manifests", "snapshots", "chunks", "other"],
  "gc_counts": ["snapshots_deleted", "manifests_deleted", "chunks_deleted", "transaction_logs_deleted", "attributes_deleted", "bytes_deleted", "objects_failed_to_delete", "delete_errors"],
  "duration_phases": ["trim", "expire", "gc", "list"],
  "documents": { "...": "Step 2" },
  "cases": [ "...Step 3" ]
}
```

- [ ] **Step 2: Generate the six `documents` from the real code, then make them illustrative.** Add the scenario builders from Step 4 first. Run each one once with a throwaway `print(json.dumps(summary, indent=2, sort_keys=True))` and paste the output under its name. Then replace timestamps with round 2026-11-03 values in the same `isoformat()` style (`2026-11-03T14:23:00.000000+00:00`), and durations and byte counts with small round integers. Keep every key and every JSON type. Do not commit the print.

These are the expected key sets. If the producer gives something else, the producer is right: say so in the PR, unless it contradicts `docs/monitoring.md`, in which case stop and report.

| document | scenario | keys (beyond `status`, `started_at`, `attention`, `durations_ms`, `finished_at`) |
|---|---|---|
| `windowed` | `max_steps: 3`, appends 0..4, `cube.run()` | `ledger_pruned`, `window: true`, `repository: true`, `trimmed`, `healed`, `expired_snapshots` (int), `gc` (all 8 `gc_counts`), `published: true`, `publish_outcome: "published"`, `sizes` (all 6 kinds), `total_objects`, `total_bytes`, `warn_bytes: 1073741824`, `measured_at`; `durations_ms` ⊇ `trim, expire, gc, list` |
| `windowless` | no window, appends 0..3, `cube.run()` | as `windowed` but `window: false`, `expired_snapshots: null`, `gc: null`; `durations_ms` = `{list}` |
| `no_repository` | `max_steps: 3`, no appends, `cube.run()` | `ledger_pruned`, `window: true`, `repository: false`; `durations_ms: {}`; no sizes |
| `failed` | no window, append 0, `storage_for` raises `ConnectionError("platform bucket unreachable")` | `status: "failed"`, `error`, `ledger_pruned`, `window: false`; **no** `repository` |
| `publish_failed` | `max_age: "30m"`, append 0..3 at `scan(3)`, `after_batch` raises `TimeoutError`, `run_cube_maintain` only at `scan(3)+25 min` | `status: "attention"`, `attention: ["publish_failed"]`, `trimmed: 2`, `published: false`, `publish_outcome: "error"`, `publish_error`, `expired_snapshots`, `gc`; no sizes |
| `measure_failed` | no window, append 0, `run_cube_maintain`, then `run_cube_measure` with `list_objects` raising | `status: "attention"`, `attention: ["measure_failed"]`, `measure_error`, `measured_at`, `published: true`; no `sizes`/`total_*`/`warn_bytes` |

- [ ] **Step 3: Write the app-only `cases[]`.** Each case is `{ name, base: "<document name>", patch?, remove?, app }`. vitest builds `{...documents[base], ...patch}` and then deletes the `remove` keys:
- accept: `unknown top-level key` (base `windowed`, patch `{"compaction": {"ran": false}}`); `unknown attention reason from a newer writer` (patch `{"status": "attention", "attention": ["manifest_bloat"]}`); `unknown publish outcome` (patch `{"publish_outcome": "deferred"}`); `unknown size kind` (patch `sizes` with an extra `"virtual_refs": {"objects": 1, "bytes": 2}` beside the six); `finished_at absent` (remove `["finished_at"]`).
- reject: `unknown status` (patch `{"status": "degraded"}`); `status missing` (remove `["status"]`); `started_at missing` (remove `["started_at"]`); `attention not a list` (patch `{"attention": "repo_size"}`); `durations_ms not an object` (patch `{"durations_ms": [1]}`); `a size kind without bytes` (patch `sizes.snapshots` to `{"objects": 2}`); `total_bytes as a string` (patch `{"total_bytes": "19316"}`); `gc as a list` (patch `{"gc": []}`).

- [ ] **Step 4: Write the failing pytest tests**

In `tests/test_contract_fixtures.py`, add `CUBE_MAINTENANCE = _load("cube-maintenance-summary.json")` and:

```python
def test_cube_maintenance_vocabulary_matches_golden():
    from pipeline.cubes import maintain
    from pipeline.cubes.collection import MISSING_COLLECTION, PUBLISHED, SUPERSEDED, UNCHANGED

    m = CUBE_MAINTENANCE
    assert m["statuses"] == [maintain.STATUS_OK, maintain.STATUS_ATTENTION, maintain.STATUS_FAILED]
    written = sorted(v for k, v in vars(maintain).items() if k.startswith("ATTENTION_"))
    assert sorted(m["attention"]) == written  # a new reason can't land unpinned
    assert m["measure_reasons"] == list(maintain.MEASURE_REASONS)
    assert m["publish_outcomes"] == [PUBLISHED, UNCHANGED, SUPERSEDED, MISSING_COLLECTION, maintain.PUBLISH_ERROR]
    assert m["size_kinds"] == [*maintain.KINDS, maintain.OTHER_KIND]
    assert m["gc_counts"] == [*maintain.GC_COUNTS, "delete_errors"]
    for doc in m["documents"].values():
        assert doc["status"] in m["statuses"] and set(doc["attention"]) <= set(m["attention"])
        assert set(doc["durations_ms"]) <= set(m["duration_phases"])
```

At the end of `tests/test_cube_maintain.py`, add one async builder per document, matching Step 2's table. Each returns `cube.sink.last_maintenance`; `failed` wraps its run in `pytest.raises(ConnectionError)`. Then add:

```python
MAINTENANCE_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/contract-fixtures/cube-maintenance-summary.json").read_text()
)
MAINTENANCE_SCENARIOS = {
    "windowed": _windowed, "windowless": _windowless, "no_repository": _no_repository,
    "failed": _failed, "publish_failed": _publish_failed, "measure_failed": _measure_failed,
}
#: values compared exactly; everything else by JSON type only
EXACT_KEYS = ("status", "attention", "window", "repository", "healed", "published",
              "publish_outcome", "trimmed", "ledger_pruned", "warn_bytes")


def _shape(value):
    """The key tree with each leaf replaced by its JSON type (bool before int)."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return {int: "int", float: "number", str: "str", list: "list"}[type(value)]


def test_every_maintenance_document_has_a_scenario():
    assert set(MAINTENANCE_SCENARIOS) == set(MAINTENANCE_FIXTURE["documents"])


@pytest.mark.parametrize("name", sorted(MAINTENANCE_SCENARIOS))
async def test_the_pipeline_writes_the_documented_maintenance_shape(tmp_path, name):
    produced = await MAINTENANCE_SCENARIOS[name](tmp_path)
    golden = MAINTENANCE_FIXTURE["documents"][name]
    assert _shape(produced) == _shape(golden)
    for key in EXACT_KEYS:
        assert produced.get(key, "<absent>") == golden.get(key, "<absent>"), key
```

Add `import json` to the file's imports.

- [ ] **Step 5: Run them.** Before Step 2's documents are pasted they fail. After Step 2 they pass.

Run: `uv run pytest -q tests/test_contract_fixtures.py -k maintenance tests/test_cube_maintain.py` → PASS.
Run: `uv run ruff check .` → clean.

- [ ] **Step 6: Commit**

```bash
git add tests/contract-fixtures/cube-maintenance-summary.json services/pipeline/tests/test_contract_fixtures.py services/pipeline/tests/test_cube_maintain.py
git commit -m "Z-8a: cube-maintenance-summary fixture, pinned against the real maintenance producer"
```

---

### Task 3: `CubeMaintenance`, the app half

**Files:**
- Modify: `app/src/lib/cubes/status.ts`, `app/src/lib/cubes/schemas.ts`, `app/src/lib/cubes/storage.ts:13-85`
- Test: `app/src/__tests__/contract-fixtures.test.ts` (new describe), `app/src/__tests__/cubes-storage.test.ts`

**Interfaces:**
- Consumes: the fixture from Task 2.
- Produces:
  - `status.ts`: `CUBE_MAINTENANCE_STATUSES = ["ok", "attention", "failed"] as const`, `CUBE_MAINTENANCE_ATTENTION` (the six, in fixture order), `CUBE_PUBLISH_OUTCOMES` (the five), `CUBE_SIZE_KINDS` (the six); the label maps `CUBE_MAINTENANCE_STATUS_LABEL`, `CUBE_MAINTENANCE_ATTENTION_LABEL` and `CUBE_PUBLISH_OUTCOME_LABEL`.
  - `schemas.ts`: `cubeMaintenanceSchema`, `type CubeMaintenance = z.output<typeof cubeMaintenanceSchema>`, and `readCubeMaintenance(raw: unknown): CubeMaintenance | null`.
  - `storage.ts`: `ApiCubeSink.last_maintenance: CubeMaintenance | null`.

- [ ] **Step 1: Write the failing tests**

In `contract-fixtures.test.ts`, add `describe("cube maintenance summary contract (tests/contract-fixtures/cube-maintenance-summary.json)")`, loaded with `loadFixture`:
- `pins the vocabularies`: `[...CUBE_MAINTENANCE_STATUSES]`, `[...CUBE_MAINTENANCE_ATTENTION]`, `[...CUBE_PUBLISH_OUTCOMES]` and `[...CUBE_SIZE_KINDS]` equal the fixture lists.
- `labels every status, attention reason and publish outcome`: each label map's sorted keys equal its sorted fixture list.
- `it.each(Object.entries(fixture.documents))("reads the %s document back to itself", …)`: `cubeMaintenanceSchema.parse(doc)` `toEqual(doc)`. Because unknown keys are stripped, any key the schema forgot fails this.
- `it.each(fixture.cases)("$app: $name", …)`: build the doc from `base` + `patch` − `remove`, then `expect(cubeMaintenanceSchema.safeParse(doc).success).toBe(c.app === "accept")`.

In `cubes-storage.test.ts`:
- `types last_maintenance from the stored summary`: mock a row whose `last_maintenance` is the fixture's `windowed` document. `(await getCubeSink("goes19-c13-cube"))!.last_maintenance?.sizes?.chunks` equals `{ objects: …, bytes: … }` from the document.
- `an unreadable stored summary is null, never a 500`: a row with `last_maintenance: { status: "degraded" }` makes `getCubeSink` resolve with `last_maintenance: null`, and a `vi.spyOn(console, "warn")` is called once. A row with `last_maintenance: null` resolves `null` and does not warn.

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/cubes-storage.test.ts` → FAIL (missing exports).

- [ ] **Step 3: Implement.** `cubeMaintenanceSchema` is a plain `z.object` (it strips unknown keys). Required fields:
- `status: z.enum(CUBE_MAINTENANCE_STATUSES)`
- `started_at: z.string()`
- `attention: z.array(z.string())`
- `durations_ms: z.record(z.string(), z.number())`

Every other key is `.optional()`:
- `finished_at`, `error`, `publish_error`, `measured_at`, `measure_error`: string.
- `window`, `repository`, `healed`, `published`: boolean.
- `ledger_pruned`, `trimmed`, `total_objects`, `total_bytes`, `warn_bytes`: `z.number().int()`.
- `expired_snapshots`: int, `.nullable()`.
- `publish_outcome`: `z.string().nullable()`.
- `gc`: `z.object({...7 int counters, delete_errors: z.array(z.string())}).nullable()`.
- `sizes`: `z.record(z.string(), z.object({ objects: int, bytes: int }))`.

`readCubeMaintenance`: `null`/`undefined` → `null`. A failed parse → `console.warn("[cubes] unreadable last_maintenance", issues)`, then `null`. In `storage.ts`, add `"last_maintenance"` to the `CubeSinkRow` `Omit` and declare it `unknown`; `toApi` calls `readCubeMaintenance`. `schemas.ts` imports the status values from `./status`, and `status.ts` takes only `import type` from `./schemas`, so there is no runtime cycle.

Labels (one line each):
- Statuses: `OK` / `Needs attention` / `Failed`.
- Attention reasons:
  - `repo_size`: "Repository at or above the size warning"
  - `gc_delete_failures`: "GC could not delete some objects"
  - `unrecorded_tip`: "Repository tip is not the recorded snapshot"
  - `publish_failed`: "Collection republish failed"
  - `expiry_skipped`: "Expiry and GC skipped this run"
  - `measure_failed`: "Size readout failed"
- Publish outcomes: `Published` / `Already current` / `Superseded by a newer snapshot` / `Cube collection missing` / `Republish failed`.

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/cubes-storage.test.ts src/__tests__/api-cube-sink.test.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/cubes/status.ts app/src/lib/cubes/schemas.ts app/src/lib/cubes/storage.ts app/src/__tests__/contract-fixtures.test.ts app/src/__tests__/cubes-storage.test.ts
git commit -m "Z-8a: type last_maintenance as CubeMaintenance, read leniently from the fixture-pinned shape"
```

---

### Task 4: `cubeSinkHealth`

**Files:**
- Modify: `app/src/lib/cubes/status.ts`
- Test: `app/src/__tests__/cubes-status.test.ts`

**Interfaces:**
- Consumes: `CubeMaintenance` (Task 3); `ApiCubeSink`, `CubeLedgerSummary` (`import type` from `./storage`); `durationSeconds` from `./schemas`.
- Produces:
  - `CUBE_SINK_HEALTH_STATES = ["disabled", "error", "attention", "stale", "waiting", "ok"] as const`, `type CubeSinkHealthState`, and `CUBE_SINK_HEALTH_LABEL`. Labels: "Disabled", "Error", "Needs attention", "No recent data", "Waiting for the first append", "Healthy".
  - `interface CubeSinkHealth { state: CubeSinkHealthState; reasons: string[] }`. The reasons are `last_error`, `maintenance_failed`, the maintenance attention reasons verbatim, `failed_rows` and `no_append_within_window`.
  - `cubeSinkHealth(sink: Pick<ApiCubeSink, "enabled" | "last_error" | "last_maintenance" | "last_appended_at" | "config">, ledger: Pick<CubeLedgerSummary, "recent">, now?: Date): CubeSinkHealth`.

- [ ] **Step 1: Write the failing tests.** Add a `describe("cubeSinkHealth")` to `cubes-status.test.ts`. It builds `sink(overrides)` with defaults `enabled: true`, `last_error: null`, `last_maintenance: null`, `last_appended_at: "2026-11-03T12:00:00.000Z"` and `config: { …, window: { max_age: "24h" } }`. The ledger default is `{ recent: [] }`, and `NOW = new Date("2026-11-03T13:00:00Z")`. Load the maintenance fixture's documents the way `contract-fixtures.test.ts` does.
- `healthy`: the defaults give `{ state: "ok", reasons: [] }`.
- `disabled wins over everything, and keeps the error as a reason`: `enabled: false, last_error: "source_collection_deleted"` gives `{ state: "disabled", reasons: ["last_error"] }`.
- `last_error is error`: gives `{ state: "error", reasons: ["last_error"] }`, even with `last_maintenance` = documents.publish_failed.
- `a failed maintenance run is error`: `last_maintenance` = documents.failed gives `{ state: "error", reasons: ["maintenance_failed"] }`.
- `maintenance attention carries its reasons`: documents.publish_failed gives `{ state: "attention", reasons: ["publish_failed"] }`.
- `a failed row among recent is attention`: recent `[{ status: "failed", reason: "crash_loop", … }]` gives `reasons` containing `"failed_rows"`. With measure_failed as well, `reasons` equals `["measure_failed", "failed_rows"]`.
- `stale once the whole max_age window has passed without an append`: `last_appended_at` 24 h + 1 ms before `NOW` gives `{ state: "stale", reasons: ["no_append_within_window"] }`. Exactly 24 h before is `ok`.
- `never stale without max_age`: `window: { max_steps: 288 }` and `last_appended_at` a week ago gives `ok`. No window at all gives `ok`.
- `waiting before the first append`: `last_appended_at: null` gives `{ state: "waiting", reasons: [] }`.
- `attention outranks stale and waiting`: a failed row, a null `last_appended_at` and documents.measure_failed together give `attention`.

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/__tests__/cubes-status.test.ts` → FAIL (`cubeSinkHealth` is not exported).

- [ ] **Step 3: Implement** the precedence from Decision 8 as an early-return chain. Staleness follows Decision 9: `durationSeconds(config.window.max_age) * 1000` compared with `now.getTime() - Date.parse(last_appended_at)`, strictly greater. `now` defaults to `new Date()`.

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/__tests__/cubes-status.test.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/cubes/status.ts app/src/__tests__/cubes-status.test.ts
git commit -m "Z-8a: cubeSinkHealth — one state for the sink card"
```

---

### Task 5: `recent` by activity, and an optimistic PATCH

**Files:**
- Modify: `app/src/lib/cubes/storage.ts` (`cubeLedgerSummary`, `setCubeSinkEnabled`)
- Modify: `app/src/pages/api/collections/[id]/cube-sink.ts` (PATCH and the module doc)
- Test: `app/src/__tests__/cubes-storage.test.ts`, `app/src/__tests__/api-cube-sink.test.ts`

**Interfaces:**
- Produces: `setCubeSinkEnabled(cubeCollectionId: string, enabled: boolean, expectedVersion: string): Promise<ApiCubeSink | null>`. It returns null when the row is gone **or** its version moved.

- [ ] **Step 1: Write the failing tests**

`cubes-storage.test.ts`:
- `orders recent ledger rows by activity`: the second query's SQL contains `ORDER BY updated_at DESC, id DESC LIMIT 20` and does not contain `ORDER BY item_datetime`.
- `the toggle applies only at the version it read`: `setCubeSinkEnabled("goes19-c13-cube", true, "2026-10-05 01:00:00.123456+00")`. The SQL contains `AND updated_at = $3::timestamptz`, and the params are `["goes19-c13-cube", true, "2026-10-05 01:00:00.123456+00"]`. An empty result resolves `null`.

`api-cube-sink.test.ts` PATCH block:
- Change the existing `toHaveBeenCalledWith(CUBE, false)` and `(CUBE, true)` assertions to `(CUBE, false, "V1")` and `(CUBE, true, "V1")`.
- `a lost toggle re-reads, re-checks the fresh row and retries once`: `getCubeSink` resolves `sink({ enabled: false, version: "V1" })`, then `sink({ enabled: false, version: "V2" })`. `setCubeSinkEnabled` resolves `null`, then `sink()`. Expect 200, `setCubeSinkEnabled` last called with `(CUBE, true, "V2")`, and `referenceIngestSources` called twice.
- `a source deleted mid-request: the re-check on the fresh row refuses`: the first read is `sink({ enabled: false })`. `setCubeSinkEnabled` resolves `null` once. The second read is `sink({ enabled: false, last_error: "source_collection_deleted", version: "V2" })`, and from the second call on `existingCollections` resolves `new Set([CUBE])`. Expect 422 `source_collection_not_found`, and `setCubeSinkEnabled` called exactly once.
- `still losing after the re-check → 409 cube_sink_conflict`: `getCubeSink` always returns a sink, and `setCubeSinkEnabled` always resolves `null`. Expect 409 with `code` `cube_sink_conflict`, and `setCubeSinkEnabled` called twice.
- `a sink deleted between attempts is a 404`: the second `getCubeSink` resolves `null`, so expect 404.

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/__tests__/cubes-storage.test.ts src/__tests__/api-cube-sink.test.ts` → FAIL.

- [ ] **Step 3: Implement.**
- `storage.ts`: change the `recent` ORDER BY, and add the `$3` version guard to `setCubeSinkEnabled`. Keep `last_error = CASE WHEN $2 THEN NULL ELSE last_error END`.
- PATCH: after the body parse, loop twice. Each attempt does `current = await getCubeSink(...)` (404 if null), then `refuseSource(identity, cube, current.source_collection_id)` when enabling, then `setCubeSinkEnabled(cube, enabled, current.version)`. Return 200 on a row.
- After the loop, return `refuse(409, "cube_sink_conflict", "The cube sink changed while this request was being applied; reload it and retry")`, the same message PUT uses (hoist it into one const).
- Update the module doc's PATCH line: "optimistic on the row version, like PUT (#98)".

- [ ] **Step 4: Run to verify they pass**

Run: `npx vitest run src/__tests__/cubes-storage.test.ts src/__tests__/api-cube-sink.test.ts` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/cubes/storage.ts "app/src/pages/api/collections/[id]/cube-sink.ts" app/src/__tests__/cubes-storage.test.ts app/src/__tests__/api-cube-sink.test.ts
git commit -m "Z-8a: recent ledger rows by activity; PATCH is optimistic on the version and re-checks a fresh row"
```

---

### Task 6: A non-S3 reference source has its own code

**Files:**
- Modify: `app/src/lib/cubes/storage.ts` (`ReferenceIngestSource`, `referenceIngestSources`)
- Modify: `app/src/lib/cubes/status.ts` (add `referenceSourceRefusal`)
- Modify: `app/src/pages/api/collections/[id]/cube-sink.ts` (`refuseSource`)
- Test: `app/src/__tests__/cubes-status.test.ts`, `app/src/__tests__/cubes-storage.test.ts`, `app/src/__tests__/api-cube-sink.test.ts`

**Interfaces:**
- Produces:
  - `ReferenceIngestSource` gains `protocol: ConnectionProtocol` (from `@/lib/connections/schemas`).
  - `storage.ts`: `const REFERENCE_INGEST_FROM` (the shared `FROM … JOIN … WHERE` fragment, without the collection predicate), reused by Task 7.
  - `status.ts`: `CUBE_SOURCE_REFUSAL_CODES = ["no_reference_ingest", "unsupported_source_protocol", "signed_source_unsupported"] as const`, `type CubeSourceRefusalCode`, and `referenceSourceRefusal(refs: readonly Pick<ReferenceIngestSource, "protocol" | "anonymous">[]): { code: CubeSourceRefusalCode; error: string } | null`. It lives in `status.ts`, not `storage.ts`, because `api-cube-sink.test.ts` mocks the whole storage module.

- [ ] **Step 1: Write the failing tests**

`cubes-status.test.ts`, in `describe("referenceSourceRefusal")`:
- `[]` gives code `no_reference_ingest`.
- `[{ protocol: "s3", anonymous: true }]` gives `null`.
- `[{ protocol: "s3", anonymous: true }, { protocol: "sftp", anonymous: false }]` gives code `unsupported_source_protocol`, and `error` contains `"sftp"`.
- `[{ protocol: "s3", anonymous: false }]` gives code `signed_source_unsupported`.
- `[{ protocol: "ftp", anonymous: false }, { protocol: "s3", anonymous: false }]` gives `unsupported_source_protocol`: the protocol check comes before the signed check.

`cubes-storage.test.ts`: extend the existing `lists only enabled, live, reference-mode ingest associations…` test. The SQL contains `c.protocol` and `c.protocol = 's3' AND COALESCE(c.config->'anonymous' = 'true'::jsonb, false)`, and the mocked row's `protocol` passes through.

`api-cube-sink.test.ts`:
- `beforeEach`: the `referenceIngestSources` mock gains `protocol: "s3"`, and so does every inline ref literal in the file.
- New PUT test `refuses a reference ingest on a non-s3 connection with its own code`: refs `[{ association_id: "a1", connection_id: "c1", protocol: "sftp", anonymous: false }]` give 422 `unsupported_source_protocol`, and `upsertCubeSink` is not called.
- PATCH `it.each`: add the row `["unsupported_source_protocol", () => …mockResolvedValue([{ …, protocol: "ftp", anonymous: false }])]`.

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/__tests__/cubes-status.test.ts src/__tests__/cubes-storage.test.ts src/__tests__/api-cube-sink.test.ts` → FAIL.

- [ ] **Step 3: Implement.**
- `referenceIngestSources` selects `c.protocol` and the s3-only `anonymous`, through `REFERENCE_INGEST_FROM` plus `AND cc.collection_id = $1`.
- `refuseSource` replaces its two inline checks with `const refusal = referenceSourceRefusal(refs); if (refusal) return refuse(422, refusal.code, refusal.error);`. Keep the existing `no_reference_ingest` and `signed_source_unsupported` messages verbatim inside `referenceSourceRefusal`.
- The new message is `Cube sinks read reference-mode ingest from s3 connections only; this source also ingests by reference over ${protocols}`.
- Add `unsupported_source_protocol` to the module doc's code list.

- [ ] **Step 4: Run to verify they pass** (same command) → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/cubes/storage.ts app/src/lib/cubes/status.ts "app/src/pages/api/collections/[id]/cube-sink.ts" app/src/__tests__/cubes-status.test.ts app/src/__tests__/cubes-storage.test.ts app/src/__tests__/api-cube-sink.test.ts
git commit -m "Z-8a: a non-s3 reference source is 422 unsupported_source_protocol, not signed_source_unsupported"
```

---

### Task 7: The source-picker endpoint

**Files:**
- Modify: `app/src/lib/cubes/storage.ts` (add `listReferenceSourceCollections`)
- Create: `app/src/pages/api/associations/reference-sources.ts`
- Test: `app/src/__tests__/cubes-storage.test.ts`, `app/src/__tests__/api-associations-reference-sources.test.ts` (create)

**Interfaces:**
- Consumes: `REFERENCE_INGEST_FROM` and `ReferenceIngestSource` (Task 6), `referenceSourceRefusal` (Task 6), `isAdmin` (`@/lib/authz/permissions`), `authzError` (`@/lib/authz/guard`), `jsonResponse` (`@/lib/http/response`).
- Produces:
  - `interface ReferenceSourceCollection { collection_id: string; title: string | null; associations: ReferenceIngestSource[] }`.
  - `listReferenceSourceCollections(groups: string[] | null): Promise<ReferenceSourceCollection[]>`, where `null` means admin (all).
  - `GET /api/associations/reference-sources` returns `200 { collections: (ReferenceSourceCollection & { refusal: CubeSourceRefusalCode | null })[] }`.

- [ ] **Step 1: Write the failing tests**

`cubes-storage.test.ts`, in `describe("listReferenceSourceCollections")`:
- `filters by the caller's groups, keeping unowned collections`: the SQL contains `LEFT JOIN stac_higher.collection_settings s ON s.collection_id = cc.collection_id` and `($1::text[] IS NULL OR s.group_id IS NULL OR s.group_id = ANY($1::text[]))`. Calling with `["earth-observation"]` gives params `[["earth-observation"]]`, and calling with `null` gives `[null]`.
- `joins live pgstac collections for the title and reuses the reference clauses`: the SQL contains `JOIN pgstac.collections pc ON pc.id = cc.collection_id`, `pc.content->>'title'`, and each clause from the existing reference-ingest test. It orders by `cc.collection_id`.
- `groups association rows per collection, in order`: three rows (`a`/s3, `a`/sftp, `b`/s3) give two collections. `a` has two associations, the title is passed through, and `collection_id` and `title` are not repeated inside `associations`.

`api-associations-reference-sources.test.ts` (`// @vitest-environment node`; `vi.mock("@/lib/cubes/storage", () => ({ listReferenceSourceCollections: vi.fn() }))`; `call()`/`authed()` helpers as in `api-cube-sink.test.ts`):
- `401s anonymous callers without listing`: 401 with `code` `unauthenticated`, and the list is not called.
- `a member lists within their groups`: `authed(["member"], ["earth-observation"])` calls with `["earth-observation"]` and gets 200.
- `an admin lists everything`: `authed(["admin"])` calls with `null`.
- `marks each collection with the refusal PUT would give`: the mock returns three collections (s3 anonymous; s3 signed; one with an sftp association). The response `refusal` values are `[null, "signed_source_unsupported", "unsupported_source_protocol"]`, and each item keeps `collection_id`, `title` and `associations[].protocol` / `anonymous`.
- `500s with the message when the query throws`.
- `is not a gated route`: `matchGatedRoute("GET", "/api/associations/reference-sources")` is `null` (Decision 4).

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/__tests__/cubes-storage.test.ts src/__tests__/api-associations-reference-sources.test.ts` → FAIL.

- [ ] **Step 3: Implement.**
- The storage function runs one query: `REFERENCE_INGEST_FROM` + the pgstac join + the settings LEFT JOIN + the group predicate, `ORDER BY cc.collection_id, cc.created_at, cc.id`. It folds the rows into collections in TS.
- The route copies `api/monitoring/flows.ts`'s shape:
  - 401 via `authzError` ("Authentication required to list cube sources").
  - `isAdmin(identity) ? null : identity.groups`.
  - Map each collection to `{ ...c, refusal: referenceSourceRefusal(c.associations)?.code ?? null }`.
  - Catch → 500 `{ error }`.
- The module doc names the group rule (Decision 3) and says the route is read-only and ungated.

- [ ] **Step 4: Run to verify they pass** (same command) → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/cubes/storage.ts app/src/pages/api/associations/reference-sources.ts app/src/__tests__/cubes-storage.test.ts app/src/__tests__/api-associations-reference-sources.test.ts
git commit -m "Z-8a: GET /api/associations/reference-sources — the cube sink's source picker"
```

---

### Task 8: Docs and the gates

**Files:**
- Modify: `docs/backend.md` (route table), `tests/contract-fixtures/README.md` (Z section), `docs/monitoring.md` ("Cube repositories" table), `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` (§3.2), `docs/FEATURES.md` (a Z-8a row after Z-7)

- [ ] **Step 1: Apply the doc edits**
- `docs/backend.md`, the `/api/collections/[id]/cube-sink` row:
  - GET: "last 20 rows **by activity** (`updated_at`); `last_maintenance` is typed (`CubeMaintenance`, shape pinned by `cube-maintenance-summary.json`)".
  - Insert `unsupported_source_protocol` (a reference ingest on a non-s3 connection) before `signed_source_unsupported` in the 422 list.
  - Say PATCH is optimistic like PUT: re-read, re-check, then 409 `cube_sink_conflict`.
- `docs/backend.md`: a new row right after it:
  `| /api/associations/reference-sources | GET | Cube sink source picker (Z-8 card): collections with an enabled reference-mode ingest association, one row each with title, associations (protocol, anonymous) and the 422 refusal code PUT /cube-sink would give (null = eligible). member+: collections you can manage (owned by your groups, or unowned); admin: all — Z-8a |`
- `tests/contract-fixtures/README.md`, Z section:
  - A `cube-maintenance-summary.json` bullet describing style `producer-shapes`: vocab lists, named `documents` that pytest regenerates from the real producer and compares by key tree, JSON type and the exact enum fields; vitest parses each document back to itself; `cases[]` are app-only (`base` + `patch` − `remove`); keys are absent ≠ null.
  - Amend the `cube-append-status.json` bullet: `appended_reasons`, `failed_reasons` (closed `crash_loop`, free text otherwise) and `failed_reason_max_chars`.
- `docs/monitoring.md`, the `expired_snapshots, gc` row: "Both are `null` without a window, and absent (like every key after `repository`) when the repository does not exist or the run failed before reaching it." After the table, add: "The shape is pinned by `tests/contract-fixtures/cube-maintenance-summary.json` in both runtimes; the app reads it as `CubeMaintenance` (`app/src/lib/cubes/schemas.ts`)."
- Spec §3.2: append *"As built (Z-8a): `duplicate` is written on `appended` rows (the step was already in the cube), so it is not a skip reason; `failed` rows carry `crash_loop` or free error text (≤ 500 chars). The fixture lists them as `appended_reasons` and `failed_reasons`."*
- `docs/FEATURES.md`: a `Z-8a · Sink API readiness` ✅ row naming the fixture, `CubeMaintenance`, `cubeSinkHealth`, `/api/associations/reference-sources`, the `recent` order, the PATCH lock and `unsupported_source_protocol`.

- [ ] **Step 2: Run the gates**

Run (repo root): `npm run verify` → typecheck, build and unit tests all green.
Run (`services/pipeline/`): `uv run pytest -q` → all pass (DB-gated tests skip without `DATABASE_URL`). Then `uv run ruff check .` → `All checks passed!`.

- [ ] **Step 3: Commit**

```bash
git add docs/backend.md tests/contract-fixtures/README.md docs/monitoring.md docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md docs/FEATURES.md
git commit -m "docs(Z-8a): route rows, fixture README, maintenance shape notes"
```

PR body (written by the lead): `Closes #129`. It lists the gates run, "lead-only steps: none" and Decisions 1–15. It also names the deviations from the issue's file list: `pipeline/cubes/config.py` changed (Decision 5), `permissions.ts` did not change (Decision 4), and `monitoring.md`, `FEATURES.md` and the spec gained notes.

---

## Self-review (done while writing)

- **Issue coverage:**
  - (1) fixture + `CubeMaintenance`: Tasks 2–3;
  - (2) ledger vocabulary: Task 1;
  - (3) `cubeSinkHealth`: Task 4;
  - (4) picker endpoint: Task 7;
  - (5) `recent` order: Task 5;
  - (6) PATCH lock: Task 5;
  - (7) non-S3 code: Task 6;
  - docs and the README: Task 8.

  The acceptance vitest cases (health, picker group filter, PATCH 409, non-S3 code) are in Tasks 4, 7, 5 and 6.
- **Names across tasks:** `CubeMaintenance`, `cubeMaintenanceSchema`, `readCubeMaintenance`, `cubeSinkHealth`, `referenceSourceRefusal`, `REFERENCE_INGEST_FROM`, `ReferenceSourceCollection`, `listReferenceSourceCollections` and `setCubeSinkEnabled(…, expectedVersion)` are spelled the same everywhere.
- **No migration:** no task touches `migrate.ts`.
- **Proportion:** only the fixture vocabulary, the pytest shape check and exact copy are spelled out. Bodies are left to signatures and tests.
