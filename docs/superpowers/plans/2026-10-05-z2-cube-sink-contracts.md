# Z-2 · Cube Sink Contracts, Migration 032 and the Sink API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the data model and API for a **cube sink**: a row binding a source collection (fed by a reference-mode ingest association) to a cube collection that will own a virtual Icechunk repository (ADR 0022). That means two cross-runtime fixtures, migration 032, `GET/PUT/PATCH/DELETE /api/collections/[id]/cube-sink`, the BFF `_cube` guard and collection-delete cleanup. No pipeline job and no UI.

**Architecture:** The config and the ledger vocabulary are cross-runtime contracts pinned by golden fixtures, read by a strict Zod writer (`app/src/lib/cubes/`) and a lenient Python reader (`pipeline/cubes/config.py`). The app owns the DDL (migration 032: `cube_sinks` + `cube_appends`). The sink route is shaped like the collection-settings route. The role and the audit row come from the middleware guard (`matchGatedRoute`). The group rule is checked in-route with `canManageCollection` on the **cube** collection (spec §14.1), and a sink outside the caller's groups is a 404. The BFF learns two things: refuse item id `_cube` in a cube collection, and delete sink rows when a collection is deleted.

**Tech Stack:** Astro 7 API routes, Zod v4 and vitest (app); Python 3.12 stdlib, pytest and ruff (pipeline); the migration lives in `app/src/lib/db/migrate.ts`; JSON golden fixtures.

**Spec:** `docs/superpowers/specs/2026-10-03-virtual-cube-sink-design.md` §3.1, §3.2, §4, §7, §13, §14.1 (slice row in §15); ADR `docs/decisions/0022-virtual-cube-sink.md`; epic #83; issue #88.

## Global Constraints

- **Worktree:** `.claude/worktrees/z2-cube-sink-contracts`, branch `feat/z2-cube-sink-contracts`, off a fresh `origin/main`, with `npm install` done. Never work in the main checkout.
- **Gates:** every task ends green on the suites it touches. The final task runs `npm run verify` (repo root), plus `cd services/pipeline && uv run pytest -q && uv run ruff check .`. No e2e, no dev server, no Docker, no load harness: the compose stack running on this machine carries the standing GOES demo and must not be touched. The migration apply-check uses a **throwaway** Postgres cluster in the session scratchpad on port 5499.
- **Migration 032** (`032_cube_sinks`), reserved on #88. 029 (K-3) and 031 (K-4) are reserved and not on `main`. Append after `030_container_images`. The pipeline runs no DDL (ADR 0001).
- **Fixture rule** (`backend-invariants`): each new fixture is consumed by `app/src/__tests__/contract-fixtures.test.ts` **and** `services/pipeline/tests/test_contract_fixtures.py`, and gets a README section, all in the same commit.
- **Config shape (spec §3.1), verbatim:** `parser` is `"hdf5"` only (`"grib"` reserved, rejected by both sides). `append_dim` is required and non-empty. `variables` holds 1–64 names. `loadable_variables` must include `append_dim`. `asset_key` defaults to `"cube"` and matches `[a-z0-9_-]{1,32}`. `window` is optional, but when present needs at least one of `max_steps` (1–10,000) and `max_age` (`^\d+[mhd]$`, ≤ 30 d). `on_late` is `"skip"` only.
- **Ledger vocabulary (spec §3.2 + §5.2):** statuses `pending | appended | skipped | failed`. Skip reasons `late | duplicate | no_source_connection | unsupported_layout | source_missing | no_datetime`.
- **PUT refusals:** HTTP **422**, body `{ error, code }`. Codes: `cube_collection_not_found`, `source_collection_not_found`, `no_reference_ingest`, `signed_source_unsupported` (§13). BFF: 422 `reserved_item_id`.
- **Audit:** resource type `cube_sink`, resource id = the cube collection id from the path. `PUT`/`PATCH` → `update`, `DELETE` → `delete`.
- **No new dependency** (npm or Python). No UI.
- **Commit trailer:** every commit message ends with
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

## Decisions this plan takes (deviations / gaps in the spec — flag in the PR)

1. **`no_datetime` joins the skip reasons.** §5.2 writes it, but §3.2's list omits it. The fixture includes it now, so Z-3 does not have to change a contract.
2. **GET is member+ of the cube collection's group, not operator+.** §7 says "operator+" for the routes. Every other config read here (settings, associations) is member-readable, the sink holds no secret, and the Z-8 card shows the ledger to its group. The mutations are operator+.
3. **Group rule on the cube collection only** (`canManageCollection`, spec §14.1). The source collection needs no group check: catalog reads are public (`DEFAULT_PUBLIC`) and the sink reads only its items.
4. **"Signed source" means any enabled, non-deleted reference-mode ingest association on the source whose connection is not `anonymous`.** One signed association makes the sink unservable for that association's items, so the PUT refuses it rather than half-serving.
5. **Layout lock (new, 409 `cube_layout_locked`).** Once the repository exists (`last_snapshot_id` set), a `PUT` may not change `parser`, `append_dim`, `variables` or `loadable_variables`. Every later append would otherwise be `skipped: unsupported_layout` with no explanation. Window, asset key, `on_late`, `enabled` and the source stay editable. The fix is to delete the cube collection, or to wait for a later slice.
6. **The Z-7 read-only DB role is NOT in 032.** A role needs `CREATEROLE` and a password the app doesn't hold. Z-7 reserves its own migration or ships it in compose init (spec §15 allows either).
7. **BFF `_cube` check queries the DB only when a written id is `_cube`,** and fails closed (503) if that query errors. Ordinary writes pay nothing.
8. **Collection-delete cleanup is best-effort after upstream success**, like the GC mark. A failure is logged and does not fail a delete the catalog has already applied.

## Review Focus

1. **An item POSTed as a FeatureCollection with one feature id `_cube` into a cube collection.** It must be refused, not just the single-item shapes (Task 5 test "refuses `_cube` inside a FeatureCollection body").
2. **A PUT to `items/_cube` whose body says a different id, or the reverse.** Both ids count (Task 5 test "checks the body id as well as the path id").
3. **Re-PUTting a sink after its repository exists, with changed `variables`.** It must be refused with a named reason, not silently accepted (Task 4 test "409s a layout change once the repository exists"). Re-PUTting the **same** layout with a new window must still pass (same task).
4. **A source with one anonymous and one signed reference association.** It must be refused with `signed_source_unsupported` (Task 4 test "refuses when any reference association is signed").
5. **A `max_age` of `0h` or `720h`.** `0h` is meaningless, and `720h` equals exactly 30 days (accepted), while `721h` is rejected. Both runtimes agree through fixture cases (Task 1).

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/contract-fixtures/cube-sink-config.json` (new) | config golden: minimal, defaults, cases |
| `tests/contract-fixtures/cube-append-status.json` (new) | pinned-enum: statuses, terminal, skip reasons |
| `tests/contract-fixtures/README.md` | new "Z queue" section |
| `app/src/lib/cubes/schemas.ts` (new) | `cubeSinkConfigSchema`, `cubeSinkPutSchema`, `cubeSinkPatchSchema`, `durationSeconds`, `layoutChanged` |
| `app/src/lib/cubes/status.ts` (new) | ledger vocabulary + label maps |
| `app/src/lib/cubes/storage.ts` (new) | all `cube_sinks` / `cube_appends` / pgstac-existence SQL |
| `app/src/lib/cubes/reserved.ts` (new) | `CUBE_ITEM_ID`, `writtenItemIds` |
| `app/src/lib/db/migrate.ts` | migration 032 |
| `app/src/lib/authz/permissions.ts` | gate `cube_sink` |
| `app/src/pages/api/collections/[id]/cube-sink.ts` (new) | the four handlers |
| `app/src/pages/api/catalog/[...path].ts` | `_cube` guard + delete cleanup |
| `services/pipeline/src/pipeline/cubes/__init__.py`, `config.py` (new) | lenient reader + constants |
| tests | `app/src/__tests__/{cubes-schemas,cubes-migration,cubes-storage,api-cube-sink,authz-cube-sink-gate}.test.ts`, `api-catalog-bff.test.ts`, `contract-fixtures.test.ts`; `services/pipeline/tests/{test_cube_config,test_contract_fixtures}.py` |
| `docs/backend.md`, `docs/FEATURES.md`, `.gitleaks.toml` | docs + secret-scan regex |

---

### Task 1: Contracts — fixtures and both runtimes' parsers

**Files:**
- Create: `tests/contract-fixtures/cube-sink-config.json`, `tests/contract-fixtures/cube-append-status.json`
- Create: `app/src/lib/cubes/schemas.ts`, `app/src/lib/cubes/status.ts`
- Create: `services/pipeline/src/pipeline/cubes/__init__.py`, `services/pipeline/src/pipeline/cubes/config.py`
- Modify: `tests/contract-fixtures/README.md`, `app/src/__tests__/contract-fixtures.test.ts`, `services/pipeline/tests/test_contract_fixtures.py`
- Test: `app/src/__tests__/cubes-schemas.test.ts`, `services/pipeline/tests/test_cube_config.py`

**Interfaces — Produces:**
- TS: `cubeSinkConfigSchema` (Zod), `type CubeSinkConfig = z.output<…>`, `durationSeconds(s: string): number | null`, `MAX_AGE_LIMIT_SECONDS = 2_592_000`, `CUBE_APPEND_STATUSES`, `CUBE_APPEND_TERMINAL`, `CUBE_SKIP_REASONS`, `CUBE_APPEND_STATUS_LABEL: Record<CubeAppendStatus, string>`, `CUBE_SKIP_REASON_LABEL: Record<CubeSkipReason, string>`.
- Py: `parse_cube_sink_config(raw) -> CubeSinkConfig` (raises `CubeSinkConfigError(ValueError)`), dataclasses `CubeSinkConfig(parser, append_dim, variables: tuple, loadable_variables: tuple, asset_key, window: CubeWindow | None, on_late)` and `CubeWindow(max_steps: int | None, max_age_seconds: int | None)`, `parse_duration(s) -> int`, constants `APPEND_STATUSES`, `TERMINAL_STATUSES`, `SKIP_REASONS`, `DEFAULT_ASSET_KEY = "cube"`.

- [ ] **Step 1: Write the fixtures**

`tests/contract-fixtures/cube-sink-config.json`:

```json
{
  "$comment": "Z-2 (virtual cube spec §3.1, ADR 0022): stac_higher.cube_sinks.config. Writer: app/src/lib/cubes/schemas.ts cubeSinkConfigSchema (strict, rejects unknown keys). Reader: services/pipeline/src/pipeline/cubes/config.py parse_cube_sink_config (lenient: ignores unknown keys, de-duplicates names). `window` absent means no trim and NO GC (ADR 0011 safety). `grib` is a reserved parser value both sides refuse until a GRIB parser exists.",
  "minimal": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"] },
  "defaults": {
    "parser": "hdf5",
    "append_dim": "t",
    "variables": ["CMI"],
    "loadable_variables": ["t"],
    "asset_key": "cube",
    "on_late": "skip"
  },
  "cases": [
    { "name": "minimal", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"] }, "app": "accept", "pipeline": "accept" },
    { "name": "full config (spec example)", "config": { "parser": "hdf5", "append_dim": "t", "variables": ["CMI", "DQF"], "loadable_variables": ["t", "x", "y", "goes_imager_projection"], "asset_key": "cube", "window": { "max_steps": 288, "max_age": "24h" }, "on_late": "skip" }, "app": "accept", "pipeline": "accept" },
    { "name": "window absent: no trim, GC never runs", "config": { "parser": "hdf5", "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"] }, "app": "accept", "pipeline": "accept" },
    { "name": "window with max_age only", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_age": "6h" } }, "app": "accept", "pipeline": "accept" },
    { "name": "max_age at the 30-day limit in hours", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_age": "720h" } }, "app": "accept", "pipeline": "accept" },
    { "name": "grib is reserved", "config": { "parser": "grib", "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"] }, "app": "reject", "pipeline": "reject" },
    { "name": "unknown parser", "config": { "parser": "netcdf3", "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"] }, "app": "reject", "pipeline": "reject" },
    { "name": "append_dim missing from loadable_variables", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["x", "y"] }, "app": "reject", "pipeline": "reject" },
    { "name": "blank append_dim", "config": { "append_dim": " ", "variables": ["CMI"], "loadable_variables": [" "] }, "app": "reject", "pipeline": "reject" },
    { "name": "no variables", "config": { "append_dim": "t", "variables": [], "loadable_variables": ["t"] }, "app": "reject", "pipeline": "reject" },
    { "name": "blank variable name", "config": { "append_dim": "t", "variables": ["CMI", ""], "loadable_variables": ["t"] }, "app": "reject", "pipeline": "reject" },
    { "name": "duplicate variable (writer rejects, reader de-duplicates)", "config": { "append_dim": "t", "variables": ["CMI", "CMI"], "loadable_variables": ["t"] }, "app": "reject", "pipeline": "accept" },
    { "name": "empty window", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": {} }, "app": "reject", "pipeline": "reject" },
    { "name": "max_steps of 0", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_steps": 0 } }, "app": "reject", "pipeline": "reject" },
    { "name": "max_steps above 10000", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_steps": 10001 } }, "app": "reject", "pipeline": "reject" },
    { "name": "max_steps not an integer", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_steps": 1.5 } }, "app": "reject", "pipeline": "reject" },
    { "name": "max_age of zero", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_age": "0h" } }, "app": "reject", "pipeline": "reject" },
    { "name": "max_age over 30 days", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_age": "721h" } }, "app": "reject", "pipeline": "reject" },
    { "name": "max_age not a duration", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_age": "24 hours" } }, "app": "reject", "pipeline": "reject" },
    { "name": "asset_key outside the grammar", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "asset_key": "Cube!" }, "app": "reject", "pipeline": "reject" },
    { "name": "on_late other than skip", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "on_late": "region_write" }, "app": "reject", "pipeline": "reject" },
    { "name": "variables not a list", "config": { "append_dim": "t", "variables": "CMI", "loadable_variables": ["t"] }, "app": "reject", "pipeline": "reject" },
    { "name": "unknown top-level key", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "chunking": "auto" }, "app": "reject", "pipeline": "accept" },
    { "name": "unknown window key", "config": { "append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"], "window": { "max_steps": 72, "keep": "latest" } }, "app": "reject", "pipeline": "accept" }
  ]
}
```

`tests/contract-fixtures/cube-append-status.json`:

```json
{
  "$comment": "Z-2 (virtual cube spec §3.2, §5.2): the stac_higher.cube_appends.status vocabulary and the closed skip-reason set. Consumed by app/src/lib/cubes/status.ts (whose label maps must cover every entry), pipeline/cubes/config.py, and migration 032's CHECK constraint (cubes-migration.test.ts). `no_datetime` is written by the dispatcher (§5.2) for an item with neither datetime nor start_datetime.",
  "style": "pinned-enum",
  "statuses": ["pending", "appended", "skipped", "failed"],
  "terminal": ["appended", "skipped", "failed"],
  "skip_reasons": ["late", "duplicate", "no_source_connection", "unsupported_layout", "source_missing", "no_datetime"]
}
```

- [ ] **Step 2: Write the failing app tests**

Append to `app/src/__tests__/contract-fixtures.test.ts` (add the imports at the top):

```ts
import { cubeSinkConfigSchema } from "@/lib/cubes/schemas";
import {
  CUBE_APPEND_STATUSES,
  CUBE_APPEND_STATUS_LABEL,
  CUBE_APPEND_TERMINAL,
  CUBE_SKIP_REASONS,
  CUBE_SKIP_REASON_LABEL,
} from "@/lib/cubes/status";

describe("cube sink config contract (tests/contract-fixtures/cube-sink-config.json)", () => {
  describeDirection("cube-sink-config.json", cubeSinkConfigSchema);
});

describe("cube append status vocabulary (tests/contract-fixtures/cube-append-status.json)", () => {
  const fixture = JSON.parse(
    readFileSync(
      fileURLToPath(new URL("../../../tests/contract-fixtures/cube-append-status.json", import.meta.url)),
      "utf8",
    ),
  ) as { statuses: string[]; terminal: string[]; skip_reasons: string[] };

  it("pins the statuses, terminal set and skip reasons", () => {
    expect([...CUBE_APPEND_STATUSES]).toEqual(fixture.statuses);
    expect([...CUBE_APPEND_TERMINAL]).toEqual(fixture.terminal);
    expect([...CUBE_SKIP_REASONS]).toEqual(fixture.skip_reasons);
  });

  it("labels every status and every skip reason", () => {
    expect(Object.keys(CUBE_APPEND_STATUS_LABEL).sort()).toEqual([...fixture.statuses].sort());
    expect(Object.keys(CUBE_SKIP_REASON_LABEL).sort()).toEqual([...fixture.skip_reasons].sort());
  });
});
```

(`describeDirection` already runs `minimal → defaults` and every case's `app` verdict.)

`app/src/__tests__/cubes-schemas.test.ts`:

```ts
// @vitest-environment node
/** Z-2: the cube sink write gate beyond the fixture cases. */
import { describe, it, expect } from "vitest";
import {
  cubeSinkConfigSchema,
  cubeSinkPatchSchema,
  cubeSinkPutSchema,
  durationSeconds,
  layoutChanged,
} from "@/lib/cubes/schemas";

const base = { append_dim: "t", variables: ["CMI"], loadable_variables: ["t"] };

describe("durationSeconds", () => {
  it("parses minutes, hours and days", () => {
    expect(durationSeconds("90m")).toBe(5400);
    expect(durationSeconds("24h")).toBe(86400);
    expect(durationSeconds("30d")).toBe(2_592_000);
  });
  it("returns null for anything outside ^\\d+[mhd]$", () => {
    for (const bad of ["", "24", "1w", "1.5h", " 1h", "-1h", "24 hours"]) {
      expect(durationSeconds(bad)).toBeNull();
    }
  });
});

describe("cubeSinkConfigSchema", () => {
  it("accepts exactly 64 variables and rejects 65", () => {
    const names = (n: number) => Array.from({ length: n }, (_, i) => `v${i}`);
    expect(cubeSinkConfigSchema.safeParse({ ...base, variables: names(64) }).success).toBe(true);
    expect(cubeSinkConfigSchema.safeParse({ ...base, variables: names(65) }).success).toBe(false);
  });
  it("accepts 30d and rejects 31d", () => {
    expect(cubeSinkConfigSchema.safeParse({ ...base, window: { max_age: "30d" } }).success).toBe(true);
    expect(cubeSinkConfigSchema.safeParse({ ...base, window: { max_age: "31d" } }).success).toBe(false);
  });
});

describe("cubeSinkPutSchema / cubeSinkPatchSchema", () => {
  it("defaults enabled to true and trims the source id", () => {
    const parsed = cubeSinkPutSchema.parse({ source_collection_id: " goes19-cmipc ", config: base });
    expect(parsed.enabled).toBe(true);
    expect(parsed.source_collection_id).toBe("goes19-cmipc");
  });
  it("rejects unknown keys and a missing source", () => {
    expect(cubeSinkPutSchema.safeParse({ config: base }).success).toBe(false);
    expect(cubeSinkPutSchema.safeParse({ source_collection_id: "s", config: base, x: 1 }).success).toBe(false);
  });
  it("PATCH takes only enabled", () => {
    expect(cubeSinkPatchSchema.safeParse({ enabled: false }).success).toBe(true);
    expect(cubeSinkPatchSchema.safeParse({ enabled: "no" }).success).toBe(false);
    expect(cubeSinkPatchSchema.safeParse({ enabled: true, config: base }).success).toBe(false);
  });
});

describe("layoutChanged", () => {
  const stored = cubeSinkConfigSchema.parse({ ...base, window: { max_steps: 72 } });
  it("ignores window, asset_key and on_late", () => {
    const next = cubeSinkConfigSchema.parse({ ...base, window: { max_age: "6h" }, asset_key: "c13" });
    expect(layoutChanged(stored, next)).toBe(false);
  });
  it("flags parser, append_dim, variables and loadable_variables", () => {
    expect(layoutChanged(stored, cubeSinkConfigSchema.parse({ ...base, variables: ["CMI", "DQF"] }))).toBe(true);
    expect(layoutChanged(stored, cubeSinkConfigSchema.parse({ ...base, loadable_variables: ["t", "x"] }))).toBe(true);
    expect(layoutChanged(stored, cubeSinkConfigSchema.parse({ append_dim: "time", variables: ["CMI"], loadable_variables: ["time"] }))).toBe(true);
  });
  it("treats a reordered variable list as unchanged", () => {
    const a = cubeSinkConfigSchema.parse({ ...base, variables: ["CMI", "DQF"] });
    const b = cubeSinkConfigSchema.parse({ ...base, variables: ["DQF", "CMI"] });
    expect(layoutChanged(a, b)).toBe(false);
  });
});
```

- [ ] **Step 3: Run them to verify they fail**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/cubes-schemas.test.ts`
Expected: FAIL, because `@/lib/cubes/schemas` cannot be resolved.

- [ ] **Step 4: Implement `app/src/lib/cubes/schemas.ts` and `status.ts`**

`app/src/lib/cubes/schemas.ts`:

```ts
/**
 * Cube sink config (virtual cube spec §3.1, ADR 0022): the strict WRITE gate
 * for stac_higher.cube_sinks.config. Pinned against the pipeline's lenient
 * reader (`pipeline/cubes/config.py`) by
 * tests/contract-fixtures/cube-sink-config.json.
 */
import { z } from "zod";

export const MAX_AGE_LIMIT_SECONDS = 30 * 86_400;
const UNIT_SECONDS = { m: 60, h: 3_600, d: 86_400 } as const;

/** `^\d+[mhd]$` → seconds, else null. */
export function durationSeconds(value: string): number | null {
  const match = /^(\d+)([mhd])$/.exec(value);
  if (!match) return null;
  return Number(match[1]) * UNIT_SECONDS[match[2] as keyof typeof UNIT_SECONDS];
}

const name = z.string().trim().min(1);
const uniqueNames = (min: number, max: number) =>
  z
    .array(name)
    .min(min)
    .max(max)
    .refine((list) => new Set(list).size === list.length, "names must be unique");

const windowSchema = z
  .strictObject({
    max_steps: z.number().int().min(1).max(10_000).optional(),
    max_age: z
      .string()
      .refine((v) => {
        const s = durationSeconds(v);
        return s !== null && s > 0 && s <= MAX_AGE_LIMIT_SECONDS;
      }, "max_age must be a duration like 24h (^\\d+[mhd]$), above zero and at most 30d")
      .optional(),
  })
  .refine(
    (w) => w.max_steps !== undefined || w.max_age !== undefined,
    "window needs max_steps, max_age or both",
  );

export const cubeSinkConfigSchema = z
  .strictObject({
    parser: z.literal("hdf5").default("hdf5"),
    append_dim: name,
    variables: uniqueNames(1, 64),
    loadable_variables: uniqueNames(1, 64),
    asset_key: z.string().regex(/^[a-z0-9_-]{1,32}$/).default("cube"),
    window: windowSchema.optional(),
    on_late: z.literal("skip").default("skip"),
  })
  .refine((c) => c.loadable_variables.includes(c.append_dim), {
    message: "loadable_variables must include append_dim",
    path: ["loadable_variables"],
  });

export type CubeSinkConfig = z.output<typeof cubeSinkConfigSchema>;

/** PUT /api/collections/[id]/cube-sink — create or replace. */
export const cubeSinkPutSchema = z.strictObject({
  source_collection_id: z.string().trim().min(1),
  config: cubeSinkConfigSchema,
  enabled: z.boolean().default(true),
});

/** PATCH /api/collections/[id]/cube-sink — toggle only. */
export const cubeSinkPatchSchema = z.strictObject({ enabled: z.boolean() });

const sorted = (list: readonly string[]) => [...list].sort().join("\u0000");

/** Whether `next` changes what the existing repository's arrays are made
 * of. Window, asset_key and on_late are not layout. */
export function layoutChanged(stored: CubeSinkConfig, next: CubeSinkConfig): boolean {
  return (
    stored.parser !== next.parser ||
    stored.append_dim !== next.append_dim ||
    sorted(stored.variables) !== sorted(next.variables) ||
    sorted(stored.loadable_variables) !== sorted(next.loadable_variables)
  );
}
```

> Zod v4 note: if `z.literal("hdf5")` rejects `"grib"` with a confusing message, that's fine. The fixture only pins accept/reject.

`app/src/lib/cubes/status.ts`:

```ts
/**
 * stac_higher.cube_appends vocabulary (virtual cube spec §3.2, §5.2). Pinned
 * by tests/contract-fixtures/cube-append-status.json against
 * pipeline/cubes/config.py and migration 032's CHECK constraint.
 */
export const CUBE_APPEND_STATUSES = ["pending", "appended", "skipped", "failed"] as const;
export type CubeAppendStatus = (typeof CUBE_APPEND_STATUSES)[number];

export const CUBE_APPEND_TERMINAL = ["appended", "skipped", "failed"] as const;

export const CUBE_SKIP_REASONS = [
  "late",
  "duplicate",
  "no_source_connection",
  "unsupported_layout",
  "source_missing",
  "no_datetime",
] as const;
export type CubeSkipReason = (typeof CUBE_SKIP_REASONS)[number];

export const CUBE_APPEND_STATUS_LABEL: Record<CubeAppendStatus, string> = {
  pending: "Pending",
  appended: "Appended",
  skipped: "Skipped",
  failed: "Failed",
};

export const CUBE_SKIP_REASON_LABEL: Record<CubeSkipReason, string> = {
  late: "Arrived after a newer step",
  duplicate: "Already in the cube",
  no_source_connection: "No reference ingest claims the file",
  unsupported_layout: "Layout differs from the cube",
  source_missing: "Source file is gone",
  no_datetime: "Item has no datetime",
};
```

- [ ] **Step 5: Run the app tests to verify they pass**

Run (from `app/`): `npx vitest run src/__tests__/contract-fixtures.test.ts src/__tests__/cubes-schemas.test.ts`
Expected: PASS.

- [ ] **Step 6: Write the failing pytest tests**

In `services/pipeline/tests/test_contract_fixtures.py`, add the import and loads, then the tests:

```python
from pipeline.cubes.config import (
    APPEND_STATUSES,
    SKIP_REASONS,
    TERMINAL_STATUSES,
    parse_cube_sink_config,
)

CUBE_SINK_CONFIG = _load("cube-sink-config.json")
CUBE_APPEND_STATUS = _load("cube-append-status.json")


@pytest.mark.parametrize("case", CUBE_SINK_CONFIG["cases"], ids=lambda c: c["name"])
def test_cube_sink_config_cases(case):
    _check(parse_cube_sink_config, case)


def test_cube_sink_config_minimal_parses_to_defaults():
    parsed = parse_cube_sink_config(CUBE_SINK_CONFIG["minimal"])
    defaults = CUBE_SINK_CONFIG["defaults"]
    assert parsed.parser == defaults["parser"]
    assert parsed.append_dim == defaults["append_dim"]
    assert list(parsed.variables) == defaults["variables"]
    assert list(parsed.loadable_variables) == defaults["loadable_variables"]
    assert parsed.asset_key == defaults["asset_key"]
    assert parsed.on_late == defaults["on_late"]
    assert parsed.window is None and "window" not in defaults


def test_cube_append_vocabulary_matches_golden():
    assert list(APPEND_STATUSES) == CUBE_APPEND_STATUS["statuses"]
    assert list(TERMINAL_STATUSES) == CUBE_APPEND_STATUS["terminal"]
    assert list(SKIP_REASONS) == CUBE_APPEND_STATUS["skip_reasons"]
```

`services/pipeline/tests/test_cube_config.py`:

```python
"""Z-2: the lenient cube sink config reader beyond the fixture cases."""

import pytest

from pipeline.cubes.config import CubeSinkConfigError, parse_cube_sink_config, parse_duration

BASE = {"append_dim": "t", "variables": ["CMI"], "loadable_variables": ["t"]}


@pytest.mark.parametrize(("text", "seconds"), [("90m", 5400), ("24h", 86400), ("30d", 2592000)])
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "24", "1w", "1.5h", " 1h", "-1h", "0m", "31d"])
def test_parse_duration_rejects(text):
    with pytest.raises(CubeSinkConfigError):
        parse_duration(text)


def test_window_converts_to_seconds():
    cfg = parse_cube_sink_config({**BASE, "window": {"max_steps": 72, "max_age": "6h"}})
    assert cfg.window is not None
    assert cfg.window.max_steps == 72
    assert cfg.window.max_age_seconds == 21600


def test_duplicates_are_dropped_in_order():
    cfg = parse_cube_sink_config({**BASE, "variables": ["CMI", "DQF", "CMI"]})
    assert cfg.variables == ("CMI", "DQF")


def test_sixty_five_variables_rejected():
    with pytest.raises(CubeSinkConfigError):
        parse_cube_sink_config({**BASE, "variables": [f"v{i}" for i in range(65)]})


def test_bool_is_not_an_integer_step_count():
    with pytest.raises(CubeSinkConfigError):
        parse_cube_sink_config({**BASE, "window": {"max_steps": True}})


def test_not_an_object():
    with pytest.raises(CubeSinkConfigError):
        parse_cube_sink_config(["t"])
```

- [ ] **Step 7: Run them to verify they fail**

Run (from `services/pipeline/`): `uv run pytest tests/test_cube_config.py tests/test_contract_fixtures.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.cubes'`.

- [ ] **Step 8: Implement `pipeline/cubes/`**

`services/pipeline/src/pipeline/cubes/__init__.py`:

```python
"""Virtual cube sink (ADR 0022). Z-2 lands the config reader and vocabulary."""
```

`services/pipeline/src/pipeline/cubes/config.py`:

```python
"""Cube sink config reader and ledger vocabulary (virtual cube spec sections 3.1, 3.2).

The LENIENT reader of ``stac_higher.cube_sinks.config``: unknown keys are
ignored and repeated names are de-duplicated. Everything that would make an
append wrong (an unknown parser, a window that cannot be applied,
``append_dim`` not loadable) is rejected. Pinned against the app's strict Zod
writer by ``tests/contract-fixtures/cube-sink-config.json``; the vocabulary by
``cube-append-status.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

APPEND_STATUSES = ("pending", "appended", "skipped", "failed")
TERMINAL_STATUSES = ("appended", "skipped", "failed")
SKIP_REASONS = (
    "late",
    "duplicate",
    "no_source_connection",
    "unsupported_layout",
    "source_missing",
    "no_datetime",
)

PARSERS = ("hdf5",)
DEFAULT_ASSET_KEY = "cube"
MAX_NAMES = 64
MAX_STEPS = 10_000
MAX_AGE_LIMIT_SECONDS = 30 * 86_400

_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNIT_SECONDS = {"m": 60, "h": 3_600, "d": 86_400}
_ASSET_KEY = re.compile(r"^[a-z0-9_-]{1,32}$")


class CubeSinkConfigError(ValueError):
    """A cube sink config the pipeline cannot run."""


@dataclass(frozen=True)
class CubeWindow:
    max_steps: int | None
    max_age_seconds: int | None


@dataclass(frozen=True)
class CubeSinkConfig:
    parser: str
    append_dim: str
    variables: tuple[str, ...]
    loadable_variables: tuple[str, ...]
    asset_key: str
    window: CubeWindow | None
    on_late: str


def parse_duration(value: Any) -> int:
    """``^\\d+[mhd]$`` to seconds, above zero and at most 30 days."""
    match = _DURATION.match(value) if isinstance(value, str) else None
    if match is None:
        raise CubeSinkConfigError("window.max_age must look like 24h (^\\d+[mhd]$)")
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)]
    if not 0 < seconds <= MAX_AGE_LIMIT_SECONDS:
        raise CubeSinkConfigError("window.max_age must be above zero and at most 30d")
    return seconds


def _names(raw: Any, field: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise CubeSinkConfigError(f"{field} must be a non-empty list")
    names: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            raise CubeSinkConfigError(f"{field} entries must be non-blank strings")
        if value.strip() not in names:
            names.append(value.strip())
    if len(names) > MAX_NAMES:
        raise CubeSinkConfigError(f"{field} allows at most {MAX_NAMES} names")
    return tuple(names)


def _window(raw: Any) -> CubeWindow | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CubeSinkConfigError("window must be an object")
    steps = raw.get("max_steps")
    if steps is not None and (
        isinstance(steps, bool) or not isinstance(steps, int) or not 1 <= steps <= MAX_STEPS
    ):
        raise CubeSinkConfigError(f"window.max_steps must be an integer in 1..{MAX_STEPS}")
    age = raw.get("max_age")
    age_seconds = parse_duration(age) if age is not None else None
    if steps is None and age_seconds is None:
        raise CubeSinkConfigError("window needs max_steps, max_age or both")
    return CubeWindow(max_steps=steps, max_age_seconds=age_seconds)


def parse_cube_sink_config(raw: Any) -> CubeSinkConfig:
    if not isinstance(raw, dict):
        raise CubeSinkConfigError("cube sink config must be an object")
    parser = raw.get("parser", "hdf5")
    if parser not in PARSERS:
        raise CubeSinkConfigError(f"unsupported parser {parser!r}")
    append_dim = raw.get("append_dim")
    if not isinstance(append_dim, str) or not append_dim.strip():
        raise CubeSinkConfigError("append_dim is required")
    append_dim = append_dim.strip()
    variables = _names(raw.get("variables"), "variables")
    loadable = _names(raw.get("loadable_variables"), "loadable_variables")
    if append_dim not in loadable:
        raise CubeSinkConfigError("loadable_variables must include append_dim")
    asset_key = raw.get("asset_key", DEFAULT_ASSET_KEY)
    if not isinstance(asset_key, str) or not _ASSET_KEY.match(asset_key):
        raise CubeSinkConfigError("asset_key must match [a-z0-9_-]{1,32}")
    on_late = raw.get("on_late", "skip")
    if on_late != "skip":
        raise CubeSinkConfigError("on_late must be 'skip'")
    return CubeSinkConfig(
        parser=parser,
        append_dim=append_dim,
        variables=variables,
        loadable_variables=loadable,
        asset_key=asset_key,
        window=_window(raw.get("window")),
        on_late=on_late,
    )
```

- [ ] **Step 9: Run pytest + ruff to verify they pass**

Run (from `services/pipeline/`): `uv run pytest tests/test_cube_config.py tests/test_contract_fixtures.py -q && uv run ruff check .`
Expected: PASS, and ruff is clean.

- [ ] **Step 10: README section**

Append to `tests/contract-fixtures/README.md`:

```markdown
## Additional fixture styles (Z queue, Z-2)

- `cube-sink-config.json` uses the ordinary `minimal`/`defaults`/`cases[]` format. It is `stac_higher.cube_sinks.config` (virtual cube spec §3.1, ADR 0022): strict in `app/src/lib/cubes/schemas.ts` `cubeSinkConfigSchema`, lenient in `pipeline/cubes/config.py` `parse_cube_sink_config`. The reader ignores unknown keys and drops repeated names, but rejects everything that would make an append wrong: an unknown or reserved parser (`grib`), a `window` with neither bound, a bound out of range, and `append_dim` missing from `loadable_variables`. `window` is absent from `defaults` on purpose: no window means no trim and no GC.
- `cube-append-status.json` is style `pinned-enum`. It holds the `cube_appends.status` vocabulary, its terminal subset and the closed skip-reason set. Three things consume it: `app/src/lib/cubes/status.ts` (whose label maps must cover every entry), `pipeline/cubes/config.py`, and migration 032's CHECK constraint (`cubes-migration.test.ts`).
```

- [ ] **Step 11: Commit**

```bash
git add tests/contract-fixtures/cube-sink-config.json tests/contract-fixtures/cube-append-status.json \
  tests/contract-fixtures/README.md app/src/lib/cubes/schemas.ts app/src/lib/cubes/status.ts \
  app/src/__tests__/contract-fixtures.test.ts app/src/__tests__/cubes-schemas.test.ts \
  services/pipeline/src/pipeline/cubes services/pipeline/tests/test_cube_config.py \
  services/pipeline/tests/test_contract_fixtures.py
git commit -m "feat(cubes): cube sink config + append status contracts (Z-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Migration 032

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append after `030_container_images`)
- Test: `app/src/__tests__/cubes-migration.test.ts`

**Interfaces — Produces:** tables `stac_higher.cube_sinks` (columns exactly as spec §4.1) and `stac_higher.cube_appends` (§4.2), with constraint names `cube_appends_status_check` and `cube_sinks_distinct_collections_check`.

- [ ] **Step 1: Write the failing migration test**

`app/src/__tests__/cubes-migration.test.ts`:

```ts
// @vitest-environment node
/**
 * Migration 032 shape pins (Z-2, virtual cube spec §4). Read as text, like the
 * 030 pins; the status CHECK is compared against cube-append-status.json.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const STATUS = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../tests/contract-fixtures/cube-append-status.json", import.meta.url)),
    "utf8",
  ),
) as { statuses: string[] };

const sql = migrationEntry("032_cube_sinks");

describe("migration 032 (cube sinks — Z-2)", () => {
  it("runs after 030 (and after 029/031 whenever those land, by name)", () => {
    expect(migrationSource.indexOf('"032_cube_sinks"')).toBeGreaterThan(
      migrationSource.indexOf('"030_container_images"'),
    );
  });

  it("creates both tables in stac_higher", () => {
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.cube_sinks (");
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.cube_appends (");
  });

  it("allows one sink per cube collection and forbids source = cube", () => {
    expect(sql).toMatch(/cube_collection_id\s+text NOT NULL UNIQUE/);
    expect(sql).toContain(
      "CONSTRAINT cube_sinks_distinct_collections_check CHECK (source_collection_id <> cube_collection_id)",
    );
  });

  it("has no FK to pgstac (collections live there)", () => {
    expect(sql).not.toMatch(/REFERENCES pgstac/);
  });

  it("constrains the ledger status to exactly the fixture vocabulary", () => {
    const match = sql.match(/cube_appends_status_check\s+CHECK \(status IN \(([^)]+)\)\)/);
    expect(match).not.toBeNull();
    expect(match![1].split(",").map((s) => s.trim().replace(/'/g, ""))).toEqual(STATUS.statuses);
  });

  it("cascades the ledger with its sink and keeps the insert idempotent", () => {
    expect(sql).toMatch(
      /cube_sink_id\s+uuid NOT NULL REFERENCES stac_higher\.cube_sinks\(id\) ON DELETE CASCADE/,
    );
    expect(sql).toContain("UNIQUE (cube_sink_id, item_id)");
  });

  it("indexes enabled sinks by source and pending rows by time", () => {
    expect(sql).toMatch(
      /cube_sinks_source_enabled_idx\s+ON stac_higher\.cube_sinks \(source_collection_id\) WHERE enabled/,
    );
    expect(sql).toMatch(
      /cube_appends_pending_idx\s+ON stac_higher\.cube_appends \(cube_sink_id, item_datetime\)\s+WHERE status = 'pending'/,
    );
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `app/`): `npx vitest run src/__tests__/cubes-migration.test.ts`
Expected: FAIL with `migration 032_cube_sinks not found`.

- [ ] **Step 3: The migration**

Append to `MIGRATIONS` in `app/src/lib/db/migrate.ts`, after the `030_container_images` entry:

```ts
  {
    // Z-2 (virtual cube spec §4, ADR 0022): a cube sink binds a source
    // collection (fed by a reference-mode ingest association) to the cube
    // collection that owns the virtual Icechunk repository at
    // assets/{cube_collection_id}/_cube/. Ownership follows the CUBE
    // collection's group (spec §14.1), so there is no group column.
    // Collections live in pgstac: no FK; the BFF collection delete removes
    // sink rows naming the deleted collection. source_prefixes,
    // last_snapshot_id and the last_* fields are pipeline-written;
    // updated_at is app-maintained. 029 (K-3) and 031 (K-4) are reserved
    // elsewhere; migrations apply by name, so the gap is harmless.
    name: "032_cube_sinks",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.cube_sinks (
        id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        source_collection_id text NOT NULL,
        cube_collection_id   text NOT NULL UNIQUE,
        enabled              boolean NOT NULL DEFAULT true,
        config               jsonb NOT NULL,
        source_prefixes      text[] NOT NULL DEFAULT '{}',
        last_snapshot_id     text,
        last_appended_at     timestamptz,
        last_maintained_at   timestamptz,
        last_maintenance     jsonb,
        last_error           text,
        created_by           text NOT NULL,
        created_at           timestamptz NOT NULL DEFAULT now(),
        updated_at           timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT cube_sinks_distinct_collections_check CHECK (source_collection_id <> cube_collection_id)
      );
      CREATE INDEX IF NOT EXISTS cube_sinks_source_enabled_idx
        ON stac_higher.cube_sinks (source_collection_id) WHERE enabled;

      -- The append ledger. UNIQUE (cube_sink_id, item_id) makes the
      -- dispatcher's insert idempotent (ON CONFLICT DO NOTHING): a
      -- transaction-API PUT arrives as delete + insert (ADR 0007).
      CREATE TABLE IF NOT EXISTS stac_higher.cube_appends (
        id            bigserial PRIMARY KEY,
        cube_sink_id  uuid NOT NULL REFERENCES stac_higher.cube_sinks(id) ON DELETE CASCADE,
        item_id       text NOT NULL,
        item_datetime timestamptz NOT NULL,
        status        text NOT NULL DEFAULT 'pending'
          CONSTRAINT cube_appends_status_check CHECK (status IN ('pending','appended','skipped','failed')),
        reason        text,
        snapshot_id   text,
        attempts      int NOT NULL DEFAULT 0,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        UNIQUE (cube_sink_id, item_id)
      );
      CREATE INDEX IF NOT EXISTS cube_appends_pending_idx
        ON stac_higher.cube_appends (cube_sink_id, item_datetime)
        WHERE status = 'pending';
    `,
  },
```

- [ ] **Step 4: Run the migration pins (plus the neighbours) to verify they pass**

Run (from `app/`): `npx vitest run src/__tests__/cubes-migration.test.ts src/__tests__/images-migration.test.ts`
Expected: PASS. 030's slice now ends at 032's `name:`, so its pins are unchanged.

- [ ] **Step 5: Apply-check on a throwaway cluster (fresh DB, and a DB at 030)**

This is not the compose DB. `$S` is the session scratchpad.

```bash
S=<scratchpad>; PG=$S/pg032; rm -rf $PG
initdb -D $PG -U postgres --auth=trust >/dev/null
pg_ctl -D $PG -o "-p 5499 -k $PG -c listen_addresses=''" -l $PG/log start
createdb -h $PG -p 5499 -U postgres fresh; createdb -h $PG -p 5499 -U postgres at030
# 1) fresh: the branch's migrations from nothing
DATABASE_URL="postgresql://postgres@/fresh?host=$PG&port=5499" \
  npx tsx -e "import('./app/src/lib/db/migrate.ts').then(m=>m.runMigrations()).then(()=>process.exit(0),e=>{console.error(e);process.exit(1)})"
# 2) at 030: main's migrations first, then the branch's
mkdir -p $S/main-db && git show origin/main:app/src/lib/db/migrate.ts > $S/main-db/migrate.ts \
  && cp app/src/lib/db/connection.ts $S/main-db/
DATABASE_URL="postgresql://postgres@/at030?host=$PG&port=5499" npx tsx -e "import('$S/main-db/migrate.ts').then(m=>m.runMigrations()).then(()=>process.exit(0),e=>{console.error(e);process.exit(1)})"
DATABASE_URL="postgresql://postgres@/at030?host=$PG&port=5499" npx tsx -e "import('./app/src/lib/db/migrate.ts').then(m=>m.runMigrations()).then(()=>process.exit(0),e=>{console.error(e);process.exit(1)})"
for db in fresh at030; do psql -h $PG -p 5499 -U postgres -d $db -Atc \
  "select name from stac_higher.migrations order by name desc limit 2; \d stac_higher.cube_appends" ; done
# constraint smoke: source=cube and a bad status both fail
psql -h $PG -p 5499 -U postgres -d fresh -c "insert into stac_higher.cube_sinks (source_collection_id,cube_collection_id,config,created_by) values ('a','a','{}','x')" ; # expect check violation
pg_ctl -D $PG stop && rm -rf $PG $S/main-db
```

Expected: both DBs list `032_cube_sinks` as applied, and the insert fails with `cube_sinks_distinct_collections_check`. **If** an earlier migration needs pgstac and fails on a bare cluster, fall back as follows: `CREATE SCHEMA stac_higher`, apply only 032's SQL twice (proving it's idempotent), and record that in the PR as the deviation. Record the outcome in the PR body either way.

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/db/migrate.ts app/src/__tests__/cubes-migration.test.ts
git commit -m "feat(cubes): migration 032 — cube_sinks + cube_appends ledger (Z-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Storage module

**Files:**
- Create: `app/src/lib/cubes/storage.ts`
- Test: `app/src/__tests__/cubes-storage.test.ts`

**Interfaces — Produces** (every function calls `runMigrations()` first, like `associations/storage.ts`):

```ts
export interface ApiCubeSink {
  id: string; source_collection_id: string; cube_collection_id: string;
  enabled: boolean; config: CubeSinkConfig; source_prefixes: string[];
  last_snapshot_id: string | null; last_appended_at: string | null;
  last_maintained_at: string | null; last_maintenance: unknown;
  last_error: string | null; created_by: string; created_at: string; updated_at: string;
}
export interface CubeLedgerRow { item_id: string; item_datetime: string; status: CubeAppendStatus; reason: string | null; snapshot_id: string | null; attempts: number; updated_at: string; }
export interface CubeLedgerSummary { counts: Record<CubeAppendStatus, number>; recent: CubeLedgerRow[]; }
export interface ReferenceIngestSource { association_id: string; connection_id: string; anonymous: boolean; }
getCubeSink(cubeCollectionId: string): Promise<ApiCubeSink | null>
upsertCubeSink(input: { cubeCollectionId: string; sourceCollectionId: string; config: CubeSinkConfig; enabled: boolean; createdBy: string }): Promise<{ sink: ApiCubeSink; created: boolean }>
setCubeSinkEnabled(cubeCollectionId: string, enabled: boolean): Promise<ApiCubeSink | null>
deleteCubeSink(cubeCollectionId: string): Promise<boolean>
deleteCubeSinksForCollection(collectionId: string): Promise<number>
deleteCubeSinksForCollectionTolerant(collectionId: string): Promise<void>
isCubeCollection(collectionId: string): Promise<boolean>
cubeLedgerSummary(sinkId: string): Promise<CubeLedgerSummary>
existingCollections(ids: string[]): Promise<Set<string>>
referenceIngestSources(collectionId: string): Promise<ReferenceIngestSource[]>
```

- [ ] **Step 1: Write the failing storage test**

`app/src/__tests__/cubes-storage.test.ts`:

```ts
// @vitest-environment node
/** Z-2: cube sink persistence — SQL shape and row mapping. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import {
  cubeLedgerSummary,
  deleteCubeSinksForCollection,
  deleteCubeSinksForCollectionTolerant,
  existingCollections,
  getCubeSink,
  isCubeCollection,
  referenceIngestSources,
  upsertCubeSink,
} from "@/lib/cubes/storage";

const mockQuery = vi.mocked(query);
const SINK_ID = "3a9f1c2e-0000-4000-8000-0000000000c1";
const config = { parser: "hdf5", append_dim: "t", variables: ["CMI"], loadable_variables: ["t"], asset_key: "cube", on_late: "skip" } as const;

const row = {
  id: SINK_ID, source_collection_id: "goes19-cmipc", cube_collection_id: "goes19-c13-cube",
  enabled: true, config, source_prefixes: [], last_snapshot_id: null, last_appended_at: null,
  last_maintained_at: null, last_maintenance: null, last_error: null, created_by: "user-1",
  created_at: new Date("2026-10-05T00:00:00Z"), updated_at: new Date("2026-10-05T01:00:00Z"),
};

const result = (rows: unknown[]) => ({ rows, rowCount: rows.length }) as never;

beforeEach(() => mockQuery.mockReset());

describe("cube sink storage", () => {
  it("maps timestamps to ISO strings", async () => {
    mockQuery.mockResolvedValueOnce(result([row]));
    const sink = await getCubeSink("goes19-c13-cube");
    expect(sink?.created_at).toBe("2026-10-05T00:00:00.000Z");
    expect(mockQuery.mock.calls[0][1]).toEqual(["goes19-c13-cube"]);
  });

  it("upserts on cube_collection_id and reports creation via xmax", async () => {
    mockQuery.mockResolvedValueOnce(result([{ ...row, created: true }]));
    const out = await upsertCubeSink({
      cubeCollectionId: "goes19-c13-cube", sourceCollectionId: "goes19-cmipc",
      config: config as never, enabled: true, createdBy: "user-1",
    });
    expect(out.created).toBe(true);
    expect(out.sink).not.toHaveProperty("created");
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toContain("ON CONFLICT (cube_collection_id) DO UPDATE");
    expect(sql).toContain("(xmax = 0) AS created");
    expect(sql).not.toMatch(/created_by\s*=\s*EXCLUDED/); // the creator survives a replace
  });

  it("deletes sinks naming a collection as source OR cube", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 2 } as never);
    expect(await deleteCubeSinksForCollection("c1")).toBe(2);
    expect(mockQuery.mock.calls[0][0]).toMatch(/source_collection_id = \$1 OR cube_collection_id = \$1/);
  });

  it("the tolerant delete swallows and logs a DB error", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    mockQuery.mockRejectedValueOnce(new Error("db down"));
    await expect(deleteCubeSinksForCollectionTolerant("c1")).resolves.toBeUndefined();
    expect(err).toHaveBeenCalled();
    err.mockRestore();
  });

  it("isCubeCollection checks any sink, enabled or not", async () => {
    mockQuery.mockResolvedValueOnce(result([{ exists: true }]));
    expect(await isCubeCollection("goes19-c13-cube")).toBe(true);
    expect(mockQuery.mock.calls[0][0]).not.toContain("enabled");
  });

  it("summarises the ledger with zero-filled counts and the last 20 rows", async () => {
    mockQuery
      .mockResolvedValueOnce(result([{ status: "appended", count: "3" }]))
      .mockResolvedValueOnce(result([]));
    const summary = await cubeLedgerSummary(SINK_ID);
    expect(summary.counts).toEqual({ pending: 0, appended: 3, skipped: 0, failed: 0 });
    expect(mockQuery.mock.calls[1][0]).toContain("LIMIT 20");
  });

  it("checks collection existence in pgstac in one query", async () => {
    mockQuery.mockResolvedValueOnce(result([{ id: "a" }]));
    expect(await existingCollections(["a", "b"])).toEqual(new Set(["a"]));
    expect(mockQuery.mock.calls[0][0]).toContain("pgstac.collections");
  });

  it("lists only enabled, live, reference-mode ingest associations with their anonymity", async () => {
    mockQuery.mockResolvedValueOnce(result([{ association_id: "a1", connection_id: "c1", anonymous: false }]));
    const out = await referenceIngestSources("goes19-cmipc");
    expect(out).toEqual([{ association_id: "a1", connection_id: "c1", anonymous: false }]);
    const sql = mockQuery.mock.calls[0][0] as string;
    for (const clause of ["cc.direction = 'ingest'", "cc.enabled", "cc.deleted_at IS NULL", "c.deleted_at IS NULL", "storage_mode' = 'reference'"]) {
      expect(sql).toContain(clause);
    }
  });
});
```


- [ ] **Step 2: Run it to verify it fails**

Run (from `app/`): `npx vitest run src/__tests__/cubes-storage.test.ts`
Expected: FAIL, because the module cannot be resolved.

- [ ] **Step 3: Implement `app/src/lib/cubes/storage.ts`**

```ts
/**
 * cube_sinks / cube_appends persistence (virtual cube spec §4, §7). The app
 * owns the rows' user-facing fields (config, source, enabled); the pipeline
 * writes source_prefixes, last_* and the ledger. Collections live in pgstac,
 * so existence is checked there and cleanup on collection delete is explicit.
 */
import { query } from "@/lib/db/connection";
import { runMigrations } from "@/lib/db/migrate";
import { iso } from "@/lib/associations/storage";
import type { CubeSinkConfig } from "./schemas";
import { CUBE_APPEND_STATUSES, type CubeAppendStatus } from "./status";

export interface ApiCubeSink {
  id: string;
  source_collection_id: string;
  cube_collection_id: string;
  enabled: boolean;
  config: CubeSinkConfig;
  source_prefixes: string[];
  last_snapshot_id: string | null;
  last_appended_at: string | null;
  last_maintained_at: string | null;
  last_maintenance: unknown;
  last_error: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

interface CubeSinkRow extends Omit<ApiCubeSink, "last_appended_at" | "last_maintained_at" | "created_at" | "updated_at"> {
  last_appended_at: Date | null;
  last_maintained_at: Date | null;
  created_at: Date;
  updated_at: Date;
}

export interface CubeLedgerRow {
  item_id: string;
  item_datetime: string;
  status: CubeAppendStatus;
  reason: string | null;
  snapshot_id: string | null;
  attempts: number;
  updated_at: string;
}

export interface CubeLedgerSummary {
  counts: Record<CubeAppendStatus, number>;
  recent: CubeLedgerRow[];
}

export interface ReferenceIngestSource {
  association_id: string;
  connection_id: string;
  anonymous: boolean;
}

const COLUMNS = `id, source_collection_id, cube_collection_id, enabled, config,
  source_prefixes, last_snapshot_id, last_appended_at, last_maintained_at,
  last_maintenance, last_error, created_by, created_at, updated_at`;

function toApi(row: CubeSinkRow): ApiCubeSink {
  return {
    id: row.id,
    source_collection_id: row.source_collection_id,
    cube_collection_id: row.cube_collection_id,
    enabled: row.enabled,
    config: row.config,
    source_prefixes: row.source_prefixes,
    last_snapshot_id: row.last_snapshot_id,
    last_appended_at: iso(row.last_appended_at),
    last_maintained_at: iso(row.last_maintained_at),
    last_maintenance: row.last_maintenance,
    last_error: row.last_error,
    created_by: row.created_by,
    created_at: iso(row.created_at),
    updated_at: iso(row.updated_at),
  };
}

export async function getCubeSink(cubeCollectionId: string): Promise<ApiCubeSink | null> {
  await runMigrations();
  const result = await query<CubeSinkRow>(
    `SELECT ${COLUMNS} FROM stac_higher.cube_sinks WHERE cube_collection_id = $1`,
    [cubeCollectionId],
  );
  return result.rows[0] ? toApi(result.rows[0]) : null;
}

/** Create or replace. A replace keeps the id, the creator, the ledger and
 * everything the pipeline wrote. */
export async function upsertCubeSink(input: {
  cubeCollectionId: string;
  sourceCollectionId: string;
  config: CubeSinkConfig;
  enabled: boolean;
  createdBy: string;
}): Promise<{ sink: ApiCubeSink; created: boolean }> {
  await runMigrations();
  const result = await query<CubeSinkRow & { created: boolean }>(
    `INSERT INTO stac_higher.cube_sinks
       (cube_collection_id, source_collection_id, config, enabled, created_by)
     VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (cube_collection_id) DO UPDATE
       SET source_collection_id = EXCLUDED.source_collection_id,
           config = EXCLUDED.config,
           enabled = EXCLUDED.enabled,
           updated_at = now()
     RETURNING ${COLUMNS}, (xmax = 0) AS created`,
    [input.cubeCollectionId, input.sourceCollectionId, JSON.stringify(input.config), input.enabled, input.createdBy],
  );
  const { created, ...row } = result.rows[0];
  return { sink: toApi(row as CubeSinkRow), created };
}

export async function setCubeSinkEnabled(
  cubeCollectionId: string,
  enabled: boolean,
): Promise<ApiCubeSink | null> {
  await runMigrations();
  const result = await query<CubeSinkRow>(
    `UPDATE stac_higher.cube_sinks SET enabled = $2, updated_at = now()
      WHERE cube_collection_id = $1 RETURNING ${COLUMNS}`,
    [cubeCollectionId, enabled],
  );
  return result.rows[0] ? toApi(result.rows[0]) : null;
}

/** The repository stays until the collection is deleted (asset_gc), so a
 * delete is reversible by re-creating the sink while the window holds. */
export async function deleteCubeSink(cubeCollectionId: string): Promise<boolean> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.cube_sinks WHERE cube_collection_id = $1`,
    [cubeCollectionId],
  );
  return (result.rowCount ?? 0) > 0;
}

export async function deleteCubeSinksForCollection(collectionId: string): Promise<number> {
  await runMigrations();
  const result = await query(
    `DELETE FROM stac_higher.cube_sinks
      WHERE source_collection_id = $1 OR cube_collection_id = $1`,
    [collectionId],
  );
  return result.rowCount ?? 0;
}

/** Collection-delete hook: never fails a delete the catalog already applied. */
export async function deleteCubeSinksForCollectionTolerant(collectionId: string): Promise<void> {
  try {
    await deleteCubeSinksForCollection(collectionId);
  } catch (err) {
    console.error(
      `[cubes] failed to delete cube sinks for deleted collection ${collectionId}:`,
      err instanceof Error ? err.message : err,
    );
  }
}

/** Whether `_cube` is a reserved item id here: any sink, enabled or not. */
export async function isCubeCollection(collectionId: string): Promise<boolean> {
  await runMigrations();
  const result = await query<{ exists: boolean }>(
    `SELECT EXISTS (SELECT 1 FROM stac_higher.cube_sinks WHERE cube_collection_id = $1) AS exists`,
    [collectionId],
  );
  return result.rows[0]?.exists === true;
}

export async function cubeLedgerSummary(sinkId: string): Promise<CubeLedgerSummary> {
  await runMigrations();
  const counts = Object.fromEntries(CUBE_APPEND_STATUSES.map((s) => [s, 0])) as Record<CubeAppendStatus, number>;
  const grouped = await query<{ status: CubeAppendStatus; count: string }>(
    `SELECT status, count(*) AS count FROM stac_higher.cube_appends
      WHERE cube_sink_id = $1 GROUP BY status`,
    [sinkId],
  );
  for (const r of grouped.rows) counts[r.status] = Number(r.count);
  const recent = await query<Omit<CubeLedgerRow, "item_datetime" | "updated_at"> & { item_datetime: Date; updated_at: Date }>(
    `SELECT item_id, item_datetime, status, reason, snapshot_id, attempts, updated_at
       FROM stac_higher.cube_appends WHERE cube_sink_id = $1
      ORDER BY item_datetime DESC LIMIT 20`,
    [sinkId],
  );
  return {
    counts,
    recent: recent.rows.map((r) => ({ ...r, item_datetime: iso(r.item_datetime), updated_at: iso(r.updated_at) })),
  };
}

export async function existingCollections(ids: string[]): Promise<Set<string>> {
  const result = await query<{ id: string }>(
    `SELECT id FROM pgstac.collections WHERE id = ANY($1::text[])`,
    [ids],
  );
  return new Set(result.rows.map((r) => r.id));
}

/** The source's enabled, live, reference-mode ingest associations and
 * whether each connection reads anonymously (spec §7, §13). */
export async function referenceIngestSources(collectionId: string): Promise<ReferenceIngestSource[]> {
  await runMigrations();
  const result = await query<ReferenceIngestSource>(
    `SELECT cc.id AS association_id, cc.connection_id,
            COALESCE(c.config->'anonymous' = 'true'::jsonb, false) AS anonymous
       FROM stac_higher.collection_connections cc
       JOIN stac_higher.connections c ON c.id = cc.connection_id AND c.deleted_at IS NULL
      WHERE cc.collection_id = $1
        AND cc.direction = 'ingest'
        AND cc.enabled
        AND cc.deleted_at IS NULL
        AND cc.config->>'storage_mode' = 'reference'`,
    [collectionId],
  );
  return result.rows;
}
```

> If importing `iso` from `@/lib/associations/storage` drags DB mocks into unrelated suites, inline a two-line `iso` here instead. Both are fine.

- [ ] **Step 4: Run it to verify it passes**

Run (from `app/`): `npx vitest run src/__tests__/cubes-storage.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/cubes/storage.ts app/src/__tests__/cubes-storage.test.ts
git commit -m "feat(cubes): cube sink storage (Z-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The sink API — gating, route, group rule, PUT validation

**Files:**
- Modify: `app/src/lib/authz/permissions.ts`
- Create: `app/src/pages/api/collections/[id]/cube-sink.ts`
- Test: `app/src/__tests__/authz-cube-sink-gate.test.ts`, `app/src/__tests__/api-cube-sink.test.ts`

**Interfaces — Consumes:** Task 1's schemas and `layoutChanged`, Task 3's storage, `canManageCollection` (`@/lib/associations/access`), `canMutate` (`@/lib/authz/permissions`), `authzError` (`@/lib/authz/guard`), `jsonResponse` (`@/lib/http/response`).
**Produces:** `GET` → 200 `{ sink: ApiCubeSink, ledger: CubeLedgerSummary }`; `PUT` → 201 (created) or 200 `{ sink }`; `PATCH` → 200 `{ sink }`; `DELETE` → 200 `{ deleted: true }`. Errors use `{ error, code? }`.

- [ ] **Step 1: Write the failing gate test**

`app/src/__tests__/authz-cube-sink-gate.test.ts`:

```ts
// @vitest-environment node
/** Z-2: the cube sink mutations are gated + audited as `cube_sink`. */
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const PATH = "/api/collections/goes19-c13-cube/cube-sink";

describe("matchGatedRoute — /api/collections/[id]/cube-sink (Z-2)", () => {
  it.each([
    ["PUT", "update"],
    ["PATCH", "update"],
    ["DELETE", "delete"],
  ])("gates %s as a cube_sink %s on the cube collection", (method, action) => {
    expect(matchGatedRoute(method, PATH)).toEqual({
      action,
      resourceType: "cube_sink",
      resourceId: "goes19-c13-cube",
    });
  });

  it("leaves the read ungated (auth enforced in-route)", () => {
    expect(matchGatedRoute("GET", PATH)).toBeNull();
  });

  it("does not shadow collection settings", () => {
    expect(matchGatedRoute("PUT", "/api/collections/goes19-c13-cube/settings")?.resourceType).toBe(
      "collection_settings",
    );
  });
});
```

- [ ] **Step 2: Write the failing route test**

`app/src/__tests__/api-cube-sink.test.ts`:

```ts
// @vitest-environment node
/**
 * /api/collections/[id]/cube-sink (Z-2, virtual cube spec §7): auth, role,
 * group isolation (404), every PUT refusal, the layout lock, PATCH/DELETE.
 * The audit row is the guard's (authz-cube-sink-gate.test.ts pins the gate).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/cubes/storage", () => ({
  getCubeSink: vi.fn(),
  upsertCubeSink: vi.fn(),
  setCubeSinkEnabled: vi.fn(),
  deleteCubeSink: vi.fn(),
  cubeLedgerSummary: vi.fn(),
  existingCollections: vi.fn(),
  referenceIngestSources: vi.fn(),
}));
vi.mock("@/lib/associations/access", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/associations/access")>()),
  canManageCollection: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));
vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { canManageCollection } from "@/lib/associations/access";
import {
  cubeLedgerSummary,
  deleteCubeSink,
  existingCollections,
  getCubeSink,
  referenceIngestSources,
  setCubeSinkEnabled,
  upsertCubeSink,
} from "@/lib/cubes/storage";
import type { ApiCubeSink } from "@/lib/cubes/storage";
import {
  DELETE as deleteRoute,
  GET as getRoute,
  PATCH as patchRoute,
  PUT as putRoute,
} from "@/pages/api/collections/[id]/cube-sink";

const CUBE = "goes19-c13-cube";
const SOURCE = "goes19-cmipc";
const EO = "earth-observation";
const config = { append_dim: "t", variables: ["CMI"], loadable_variables: ["t"] };
const parsedConfig = { ...config, parser: "hdf5", asset_key: "cube", on_late: "skip" };

function sink(overrides: Partial<ApiCubeSink> = {}): ApiCubeSink {
  return {
    id: "3a9f1c2e-0000-4000-8000-0000000000c1", source_collection_id: SOURCE,
    cube_collection_id: CUBE, enabled: true, config: parsedConfig as never, source_prefixes: [],
    last_snapshot_id: null, last_appended_at: null, last_maintained_at: null,
    last_maintenance: null, last_error: null, created_by: "user-1",
    created_at: "2026-10-05T00:00:00.000Z", updated_at: "2026-10-05T00:00:00.000Z",
    ...overrides,
  };
}

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return { authenticated: true, mode: "bypass", identity: { sub: "user-1", email: null, name: null, groups, roles } };
}
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };
const operator = authed(["operator"]);

type RouteHandler = (ctx: never) => Promise<Response> | Response;
function call(handler: RouteHandler, auth: AuthContext, { method = "GET", body }: { method?: string; body?: unknown } = {}) {
  const url = new URL(`http://localhost:4321/api/collections/${CUBE}/cube-sink`);
  return handler({
    url, locals: { auth },
    request: new Request(url, { method, body: body === undefined ? undefined : JSON.stringify(body) }),
    params: { id: CUBE },
  } as never);
}
const put = (body: unknown, auth = operator) => call(putRoute, auth, { method: "PUT", body });

beforeEach(() => {
  vi.mocked(canManageCollection).mockReset().mockResolvedValue(true);
  vi.mocked(getCubeSink).mockReset().mockResolvedValue(null);
  vi.mocked(upsertCubeSink).mockReset().mockResolvedValue({ sink: sink(), created: true });
  vi.mocked(setCubeSinkEnabled).mockReset().mockResolvedValue(sink({ enabled: false }));
  vi.mocked(deleteCubeSink).mockReset().mockResolvedValue(true);
  vi.mocked(cubeLedgerSummary).mockReset().mockResolvedValue({ counts: { pending: 0, appended: 0, skipped: 0, failed: 0 }, recent: [] });
  vi.mocked(existingCollections).mockReset().mockResolvedValue(new Set([CUBE, SOURCE]));
  vi.mocked(referenceIngestSources).mockReset().mockResolvedValue([{ association_id: "a1", connection_id: "c1", anonymous: true }]);
});

describe("GET", () => {
  it("401s anonymous callers", async () => {
    expect((await call(getRoute, anon)).status).toBe(401);
  });
  it("returns the sink and its ledger to a member of the group", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink());
    const res = await call(getRoute, authed(["member"]));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.sink.cube_collection_id).toBe(CUBE);
    expect(body.ledger.counts.appended).toBe(0);
  });
  it("404s outside the caller's groups, even when a sink exists", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink());
    vi.mocked(canManageCollection).mockResolvedValue(false);
    expect((await call(getRoute, operator)).status).toBe(404);
    expect(cubeLedgerSummary).not.toHaveBeenCalled();
  });
  it("404s when there is no sink", async () => {
    expect((await call(getRoute, operator)).status).toBe(404);
  });
});

describe("PUT", () => {
  it("401s anonymous and 403s members (the non-mutating role)", async () => {
    expect((await put({ source_collection_id: SOURCE, config }, anon)).status).toBe(401);
    const res = await put({ source_collection_id: SOURCE, config }, authed(["member"]));
    expect(res.status).toBe(403);
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("404s a cube collection outside the caller's groups", async () => {
    vi.mocked(canManageCollection).mockResolvedValue(false);
    expect((await put({ source_collection_id: SOURCE, config })).status).toBe(404);
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("400s an invalid config and a source equal to the cube", async () => {
    expect((await put({ source_collection_id: SOURCE, config: { ...config, parser: "grib" } })).status).toBe(400);
    expect((await put({ source_collection_id: CUBE, config })).status).toBe(400);
  });
  it.each([
    ["cube_collection_not_found", () => vi.mocked(existingCollections).mockResolvedValue(new Set([SOURCE]))],
    ["source_collection_not_found", () => vi.mocked(existingCollections).mockResolvedValue(new Set([CUBE]))],
    ["no_reference_ingest", () => vi.mocked(referenceIngestSources).mockResolvedValue([])],
    ["signed_source_unsupported", () => vi.mocked(referenceIngestSources).mockResolvedValue([{ association_id: "a1", connection_id: "c1", anonymous: false }])],
  ])("422s %s", async (code, arrange) => {
    arrange();
    const res = await put({ source_collection_id: SOURCE, config });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe(code);
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("refuses when any reference association is signed", async () => {
    vi.mocked(referenceIngestSources).mockResolvedValue([
      { association_id: "a1", connection_id: "c1", anonymous: true },
      { association_id: "a2", connection_id: "c2", anonymous: false },
    ]);
    expect((await (await put({ source_collection_id: SOURCE, config })).json()).code).toBe("signed_source_unsupported");
  });
  it("creates (201) with the caller as creator and the parsed config", async () => {
    const res = await put({ source_collection_id: SOURCE, config });
    expect(res.status).toBe(201);
    expect(upsertCubeSink).toHaveBeenCalledWith({
      cubeCollectionId: CUBE, sourceCollectionId: SOURCE, config: parsedConfig, enabled: true, createdBy: "user-1",
    });
  });
  it("replaces (200) an existing sink", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink());
    vi.mocked(upsertCubeSink).mockResolvedValue({ sink: sink(), created: false });
    expect((await put({ source_collection_id: SOURCE, config })).status).toBe(200);
  });
  it("409s a layout change once the repository exists", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink({ last_snapshot_id: "SNAP1" }));
    const res = await put({ source_collection_id: SOURCE, config: { ...config, variables: ["CMI", "DQF"] } });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("cube_layout_locked");
  });
  it("still accepts a window change once the repository exists", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink({ last_snapshot_id: "SNAP1" }));
    vi.mocked(upsertCubeSink).mockResolvedValue({ sink: sink(), created: false });
    const res = await put({ source_collection_id: SOURCE, config: { ...config, window: { max_steps: 72 } } });
    expect(res.status).toBe(200);
  });
});

describe("PATCH", () => {
  it("403s members, 404s outside the group, toggles enabled", async () => {
    expect((await call(patchRoute, authed(["member"]), { method: "PATCH", body: { enabled: false } })).status).toBe(403);
    vi.mocked(canManageCollection).mockResolvedValueOnce(false);
    expect((await call(patchRoute, operator, { method: "PATCH", body: { enabled: false } })).status).toBe(404);
    const res = await call(patchRoute, operator, { method: "PATCH", body: { enabled: false } });
    expect(res.status).toBe(200);
    expect(setCubeSinkEnabled).toHaveBeenCalledWith(CUBE, false);
  });
  it("400s anything but {enabled}, 404s a missing sink", async () => {
    expect((await call(patchRoute, operator, { method: "PATCH", body: { config } })).status).toBe(400);
    vi.mocked(setCubeSinkEnabled).mockResolvedValue(null);
    expect((await call(patchRoute, operator, { method: "PATCH", body: { enabled: true } })).status).toBe(404);
  });
});

describe("DELETE", () => {
  it("403s members, 404s outside the group and a missing sink, deletes otherwise", async () => {
    expect((await call(deleteRoute, authed(["member"]), { method: "DELETE" })).status).toBe(403);
    vi.mocked(canManageCollection).mockResolvedValueOnce(false);
    expect((await call(deleteRoute, operator, { method: "DELETE" })).status).toBe(404);
    vi.mocked(deleteCubeSink).mockResolvedValueOnce(false);
    expect((await call(deleteRoute, operator, { method: "DELETE" })).status).toBe(404);
    const res = await call(deleteRoute, operator, { method: "DELETE" });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ deleted: true });
  });
});
```


- [ ] **Step 3: Run both to verify they fail**

Run (from `app/`): `npx vitest run src/__tests__/authz-cube-sink-gate.test.ts src/__tests__/api-cube-sink.test.ts`
Expected: FAIL. The gate returns null, and the route module is missing.

- [ ] **Step 4: Gate the route**

In `app/src/lib/authz/permissions.ts`, directly after the `collSettings` block:

```ts
  // Z-2 (virtual cube spec §7): the cube sink on the cube collection [id].
  // Group ownership (the cube collection's, §14.1) is enforced in-route.
  const cubeSink = path.match(/^\/api\/collections\/([^/]+)\/cube-sink$/);
  if (cubeSink && (m === "PUT" || m === "PATCH" || m === "DELETE")) {
    return {
      action: m === "DELETE" ? "delete" : "update",
      resourceType: "cube_sink",
      resourceId: cubeSink[1],
    };
  }
```

- [ ] **Step 5: The route**

`app/src/pages/api/collections/[id]/cube-sink.ts`:

```ts
/**
 * /api/collections/[id]/cube-sink — the virtual cube sink whose CUBE
 * collection is [id] (virtual cube spec §7, ADR 0022).
 *
 * GET    — member+ of the cube collection's group: the sink + ledger summary.
 * PUT    — operator+: create or replace. Both collections must exist, the
 *          source must have an enabled reference-mode ingest association,
 *          and every such association must read anonymously (§13) — else
 *          422 with a `code`. Once the repository exists the layout
 *          (parser, append_dim, variables, loadable_variables) is locked
 *          (409 cube_layout_locked).
 * PATCH  — operator+: { enabled }.
 * DELETE — operator+: removes the row; the repository stays until the
 *          collection is deleted (asset_gc), so this is reversible.
 *
 * Group rule (§14.1): the sink follows the cube collection's ownership
 * (`canManageCollection`). Outside the caller's groups every verb is a 404.
 * Role + audit (`cube_sink`) live in the guard; re-checked here.
 */
import type { APIRoute } from "astro";
import type { AuthContext } from "@/lib/auth/types";
import { authzError } from "@/lib/authz/guard";
import { canMutate } from "@/lib/authz/permissions";
import { canManageCollection } from "@/lib/associations/access";
import { jsonResponse } from "@/lib/http/response";
import { cubeSinkPatchSchema, cubeSinkPutSchema, layoutChanged } from "@/lib/cubes/schemas";
import {
  cubeLedgerSummary,
  deleteCubeSink,
  existingCollections,
  getCubeSink,
  referenceIngestSources,
  setCubeSinkEnabled,
  upsertCubeSink,
} from "@/lib/cubes/storage";

const notFound = () => jsonResponse(404, { error: "Cube sink not found" });
const refuse = (status: number, code: string, error: string) => jsonResponse(status, { error, code });

/** 401 / 403 / 404 preamble shared by every verb. Returns the caller's sub. */
async function preamble(
  auth: AuthContext | undefined,
  collectionId: string | undefined,
  requireOperator: boolean,
): Promise<{ sub: string; collectionId: string } | { response: Response }> {
  if (!auth?.authenticated) {
    return { response: authzError(401, "unauthenticated", "Authentication required for this action") };
  }
  if (requireOperator && !canMutate(auth.identity)) {
    return { response: authzError(403, "forbidden", "This action requires the operator or admin role") };
  }
  if (!collectionId || !(await canManageCollection(auth.identity, collectionId))) {
    return { response: notFound() };
  }
  return { sub: auth.identity.sub, collectionId };
}

function failure(err: unknown): Response {
  return jsonResponse(500, { error: err instanceof Error ? err.message : "Unknown error" });
}

export const GET: APIRoute = async ({ params, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, false);
    if ("response" in pre) return pre.response;
    const sink = await getCubeSink(pre.collectionId);
    if (!sink) return notFound();
    return jsonResponse(200, { sink, ledger: await cubeLedgerSummary(sink.id) });
  } catch (err) {
    return failure(err);
  }
};

export const PUT: APIRoute = async ({ params, request, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, true);
    if ("response" in pre) return pre.response;
    const cube = pre.collectionId;

    const parsed = cubeSinkPutSchema.safeParse(await request.json().catch(() => null));
    if (!parsed.success) {
      return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
    }
    const { source_collection_id: source, config, enabled } = parsed.data;
    if (source === cube) {
      return jsonResponse(400, { error: "source_collection_id must differ from the cube collection" });
    }

    const existing = await existingCollections([cube, source]);
    if (!existing.has(cube)) {
      return refuse(422, "cube_collection_not_found", `Collection '${cube}' does not exist`);
    }
    if (!existing.has(source)) {
      return refuse(422, "source_collection_not_found", `Source collection '${source}' does not exist`);
    }
    const refs = await referenceIngestSources(source);
    if (refs.length === 0) {
      return refuse(422, "no_reference_ingest",
        `Source collection '${source}' has no enabled reference-mode ingest association`);
    }
    if (refs.some((r) => !r.anonymous)) {
      return refuse(422, "signed_source_unsupported",
        "Cube sinks support anonymous (public) source connections only in v1");
    }

    const current = await getCubeSink(cube);
    if (current?.last_snapshot_id && layoutChanged(current.config, config)) {
      return refuse(409, "cube_layout_locked",
        "The cube repository already exists; parser, append_dim, variables and loadable_variables cannot change");
    }

    const { sink, created } = await upsertCubeSink({
      cubeCollectionId: cube, sourceCollectionId: source, config, enabled, createdBy: pre.sub,
    });
    return jsonResponse(created ? 201 : 200, { sink });
  } catch (err) {
    return failure(err);
  }
};

export const PATCH: APIRoute = async ({ params, request, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, true);
    if ("response" in pre) return pre.response;
    const parsed = cubeSinkPatchSchema.safeParse(await request.json().catch(() => null));
    if (!parsed.success) {
      return jsonResponse(400, { error: "Validation failed", details: parsed.error.issues });
    }
    const sink = await setCubeSinkEnabled(pre.collectionId, parsed.data.enabled);
    return sink ? jsonResponse(200, { sink }) : notFound();
  } catch (err) {
    return failure(err);
  }
};

export const DELETE: APIRoute = async ({ params, locals }) => {
  try {
    const pre = await preamble(locals.auth, params.id, true);
    if ("response" in pre) return pre.response;
    return (await deleteCubeSink(pre.collectionId)) ? jsonResponse(200, { deleted: true }) : notFound();
  } catch (err) {
    return failure(err);
  }
};
```

- [ ] **Step 6: Run both to verify they pass**

Run (from `app/`): `npx vitest run src/__tests__/authz-cube-sink-gate.test.ts src/__tests__/api-cube-sink.test.ts src/__tests__/authz-collection-settings-gate.test.ts`
Expected: PASS. The hook's `astro check` must also be clean.

- [ ] **Step 7: Commit**

```bash
git add app/src/lib/authz/permissions.ts "app/src/pages/api/collections/[id]/cube-sink.ts" \
  app/src/__tests__/authz-cube-sink-gate.test.ts app/src/__tests__/api-cube-sink.test.ts
git commit -m "feat(cubes): /api/collections/[id]/cube-sink — gated, audited, group-scoped (Z-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: BFF — the `_cube` guard and collection-delete cleanup

**Files:**
- Create: `app/src/lib/cubes/reserved.ts`
- Modify: `app/src/pages/api/catalog/[...path].ts`
- Test: `app/src/__tests__/api-catalog-bff.test.ts` (add a `vi.mock("@/lib/cubes/storage", …)` and two describes)

**Interfaces — Consumes:** `isCubeCollection`, `deleteCubeSinksForCollectionTolerant` (Task 3).
**Produces:** `CUBE_ITEM_ID = "_cube"`; `writtenItemIds(pathItemId: string | null, doc: unknown): string[]`.

- [ ] **Step 1: Write the failing tests**

In `app/src/__tests__/api-catalog-bff.test.ts`, add next to the other mocks:

```ts
vi.mock("@/lib/cubes/storage", () => ({
  isCubeCollection: vi.fn(async () => false),
  deleteCubeSinksForCollectionTolerant: vi.fn(async () => {}),
}));
```

Import `isCubeCollection` and `deleteCubeSinksForCollectionTolerant` from `@/lib/cubes/storage`, reset them in `beforeEach` (`mockReset().mockResolvedValue(false)` / `mockReset().mockResolvedValue(undefined)`), and append the describes below. They use the file's existing request helper. Read its signature first and adapt the `call(...)` lines to it; the assertions are what matters.

```ts
describe("reserved item id _cube (Z-2, virtual cube spec §7)", () => {
  const item = (id: string) => ({ type: "Feature", id, collection: "cube", geometry: null, properties: {}, links: [], assets: {} });

  it("refuses POSTing item _cube into a cube collection (422, not forwarded)", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    const res = await call("POST", "collections/cube/items", item("_cube"));
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("reserved_item_id");
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("refuses _cube inside a FeatureCollection body", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    const res = await call("POST", "collections/cube/items", { type: "FeatureCollection", features: [item("a"), item("_cube")] });
    expect(res.status).toBe(422);
  });

  it("checks the body id as well as the path id", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    expect((await call("PUT", "collections/cube/items/_cube", item("other"))).status).toBe(422);
    expect((await call("PUT", "collections/cube/items/other", item("_cube"))).status).toBe(422);
  });

  it("allows _cube in a collection that is not a cube", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(false);
    expect((await call("POST", "collections/plain/items", item("_cube"))).status).toBe(200);
  });

  it("never queries the sink table for ordinary ids", async () => {
    await call("POST", "collections/cube/items", item("a"));
    expect(isCubeCollection).not.toHaveBeenCalled();
  });

  it("fails closed (503) when the sink lookup errors", async () => {
    vi.mocked(isCubeCollection).mockRejectedValue(new Error("db down"));
    expect((await call("POST", "collections/cube/items", item("_cube"))).status).toBe(503);
  });

  it("still allows deleting an item named _cube", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    expect((await call("DELETE", "collections/cube/items/_cube")).status).toBe(200);
  });
});

describe("collection delete removes cube sinks (Z-2)", () => {
  it("deletes sink rows naming the collection after a successful delete", async () => {
    await call("DELETE", "collections/goes19-cmipc");
    expect(deleteCubeSinksForCollectionTolerant).toHaveBeenCalledWith("goes19-cmipc");
  });
  it("does not touch sinks when the upstream delete failed", async () => {
    vi.mocked(safeFetch).mockResolvedValue(upstream(500) as never);
    await call("DELETE", "collections/goes19-cmipc");
    expect(deleteCubeSinksForCollectionTolerant).not.toHaveBeenCalled();
  });
  it("does not touch sinks on an item delete", async () => {
    await call("DELETE", "collections/goes19-cmipc/items/i1");
    expect(deleteCubeSinksForCollectionTolerant).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run them to verify they fail**

Run (from `app/`): `npx vitest run src/__tests__/api-catalog-bff.test.ts`
Expected: the new cases FAIL (422s come back as 200, and the delete hook is never called). The existing cases still pass.

- [ ] **Step 3: `app/src/lib/cubes/reserved.ts`**

```ts
/**
 * `_cube` is a reserved item id in a cube collection (ADR 0022): the
 * repository lives at assets/{cube}/_cube/, so an item of that id would own
 * the repository's prefix for GC and serving.
 */
export const CUBE_ITEM_ID = "_cube";

/** Every item id a catalog write names: the path id plus the body's `id`, or
 * each feature's `id` for a FeatureCollection. */
export function writtenItemIds(pathItemId: string | null, doc: unknown): string[] {
  const ids: string[] = [];
  if (pathItemId) {
    ids.push(pathItemId);
    try {
      ids.push(decodeURIComponent(pathItemId));
    } catch {
      // malformed escape: the raw id is all there is
    }
  }
  const idOf = (value: unknown) => {
    const id = (value as { id?: unknown } | null)?.id;
    if (typeof id === "string") ids.push(id);
  };
  if (doc && typeof doc === "object") {
    const features = (doc as { features?: unknown }).features;
    if (Array.isArray(features)) features.forEach(idOf);
    else idOf(doc);
  }
  return ids;
}
```

- [ ] **Step 4: Wire the BFF**

In `app/src/pages/api/catalog/[...path].ts`:

1. Imports:

```ts
import { CUBE_ITEM_ID, writtenItemIds } from "@/lib/cubes/reserved";
import { deleteCubeSinksForCollectionTolerant, isCubeCollection } from "@/lib/cubes/storage";
```

2. Inside `if (method !== "DELETE") { … }`, directly after the `doc` parse `try/catch` and **before** the `hasStagedHrefs` branch:

```ts
    // Z-2 (virtual cube spec §7): `_cube` is reserved in a cube collection.
    // The sink table is consulted only when a written id IS `_cube`, and a
    // failed lookup fails closed — the alternative is an item that owns the
    // repository's prefix.
    if (isItemWrite && ids && writtenItemIds(ids.item, doc).includes(CUBE_ITEM_ID)) {
      let reserved: boolean;
      try {
        reserved = await isCubeCollection(ids.collection);
      } catch {
        return jsonResponse(503, {
          error: "Cube sinks are unavailable — an item named _cube cannot be checked",
        });
      }
      if (reserved) {
        return jsonResponse(422, {
          error: `Item id '${CUBE_ITEM_ID}' is reserved in cube collection '${ids.collection}'`,
          code: "reserved_item_id",
        });
      }
    }
```

3. In the post-response delete block, after `markAssetGcTolerant(...)`:

```ts
    // Z-2: a deleted collection takes its cube sinks with it, as source or
    // cube. The repository bytes ride the collection_delete GC mark above.
    if (txn.resourceType === "catalog_collection") {
      await deleteCubeSinksForCollectionTolerant(ids.collection);
    }
```

- [ ] **Step 5: Run to verify they pass**

Run (from `app/`): `npx vitest run src/__tests__/api-catalog-bff.test.ts src/__tests__/api-catalog-push.test.ts`
Expected: PASS. If `api-catalog-push.test.ts` errors on the unmocked `@/lib/cubes/storage`, add the same `vi.mock` there. Its writes never use `_cube`, but a collection delete there would now call the hook.

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/cubes/reserved.ts "app/src/pages/api/catalog/[...path].ts" \
  app/src/__tests__/api-catalog-bff.test.ts app/src/__tests__/api-catalog-push.test.ts
git commit -m "feat(cubes): BFF reserves _cube in cube collections; collection delete drops sinks (Z-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs, secret-scan regex, gates, PR

**Files:**
- Modify: `docs/backend.md`, `docs/FEATURES.md`, `.gitleaks.toml`

- [ ] **Step 1: `.gitleaks.toml`**

Line 28: `-M\d+_G` → `-M\d+(C\d+)?_G`:

```toml
  '''OR_ABI-L2-[A-Za-z0-9]+-M\d+(C\d+)?_G\d+_s\d+_e\d+_c\d+(\.nc)?''',
```

Check: `echo 'OR_ABI-L2-CMIPC-M6C13_G19_s20262761701173_e20262761703546_c20262761704044.nc' | grep -E 'OR_ABI-L2-[A-Za-z0-9]+-M[0-9]+(C[0-9]+)?_G[0-9]+_s[0-9]+_e[0-9]+_c[0-9]+(\.nc)?'` prints the name. If `gitleaks` is installed, `gitleaks detect --no-banner --redact` comes back clean too.

- [ ] **Step 2: `docs/backend.md`**

After the `/api/collections/[id]/settings/impact` row:

```markdown
| `/api/collections/[id]/cube-sink` | GET, PUT, PATCH, DELETE | Virtual cube sink on the cube collection `[id]` (ADR 0022, virtual cube spec §7). GET member+ (sink + ledger counts and last 20 rows); PUT/PATCH/DELETE operator+ audited (`cube_sink`). Group rule = the cube collection's (outside your groups → 404). PUT 422 `code`: `cube_collection_not_found`, `source_collection_not_found`, `no_reference_ingest`, `signed_source_unsupported`; 409 `cube_layout_locked` once the repository exists. DELETE leaves the repository for `asset_gc` — Z-2 |
```

In the `/api/catalog/[...path]` row, append: "Item id `_cube` in a cube collection → 422 `reserved_item_id`; a collection delete also deletes cube sinks naming it (Z-2)." Also add `cube_sinks` / `cube_appends` wherever backend.md lists `stac_higher` tables, if it keeps such a list (`grep -n "image_scans" docs/backend.md` finds it).

- [ ] **Step 3: `docs/FEATURES.md`**

Add a row under the newest section, matching its format (`grep -n "C-1\|Z-1" docs/FEATURES.md`): "Z-2 · Cube sink API and contracts. `/api/collections/[id]/cube-sink`, migration 032 (`cube_sinks`, `cube_appends`), fixtures `cube-sink-config.json` / `cube-append-status.json`, BFF `_cube` reservation. No pipeline job or UI yet (Z-3/Z-4/Z-8)."

- [ ] **Step 4: Full gates**

```bash
npm run verify                                     # repo root
(cd services/pipeline && uv run pytest -q && uv run ruff check .)
```

Expected: all green. Paste the summary lines into the PR body.

- [ ] **Step 5: Commit, push, PR**

```bash
git add docs/backend.md docs/FEATURES.md .gitleaks.toml
git commit -m "docs(cubes): sink routes + BFF reservation; gitleaks single-band GOES names (Z-2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/z2-cube-sink-contracts
gh pr create --base main --title "Z-2: cube sink contracts, migration 032 and the sink API" --body-file <scratch>/pr-body.md
```

The PR body starts with `Closes #88`. It lists the gates run with their results, the migration apply-check outcome (fresh + at-030), the lead-only steps (none per #88), and the deviations (this plan's "Decisions" 1, 2, 5, 6). It ends with the Claude Code attribution line.

- [ ] **Step 6: After merge**

`gh issue edit 89 --remove-label blocked --add-label ready`, then `git worktree remove .claude/worktrees/z2-cube-sink-contracts`.
