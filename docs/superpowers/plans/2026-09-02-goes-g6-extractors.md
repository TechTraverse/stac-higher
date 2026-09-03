# G-6 · Extractors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An ingest association can name an operator-authored *extractor* process that fixes up each draft item (datetime, geometry, properties) before it is written to the catalog — through the run ledger, with coalescing, limits, logs and failure semantics the platform already has for transforms.

**Architecture:** A process gains an immutable `kind` (`transform` | `extractor`). ITEMIZE splits at the `build_item` → `validate_item` seam: for `metadata.strategy: "extractor"` it marks the group's ledger rows `extracting`, triggers the extractor run with the draft item carried in `input_items` (coalesced per `(process, association)`), and returns. The run receives the draft through the ADR 0018 manifest (`kind: "extract"`) and writes one `{item_id}.json` back. Finalize's extract branch checks id/collection/asset-href immutability and hands the document to the shared ITEMIZE continuation (`complete_item`: validate → upsert → mark itemized → post-ingest). A run that dies fails every ledger row in its batch with the run's error; a sweep fails rows whose run vanished. The spec's §15 addendum records every decision that differs from §6.

**Tech Stack:** Python 3.12 (psycopg, Procrastinate), pytest + ruff; Astro/React 19 + TanStack Query + Zod v4 (vitest); one app migration (TypeScript, `app/src/lib/db/migrate.ts`); contract fixtures shared by both suites.

**Spec:** `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` §6 and §15 (the addendum wins where they differ).

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/goes-g6 -b ai/goes-g6 ai/main`; `npm install` at the worktree root; `cd services/pipeline && uv sync --extra dev`.
- Gates: `npm run verify` (repo root) and `uv run pytest` + `uv run ruff check .` (from `services/pipeline/`). No e2e, no dev server, no Docker for the implementer; the lead runs the live check (Task 13).
- The app owns DDL (ADR 0001): everything schema-shaped is **migration 027**, appended to `MIGRATIONS` in `app/src/lib/db/migrate.ts`. The pipeline never issues DDL. K-3 in `TODO.md` renumbers to 028 (Task 12).
- Cross-runtime shapes move together: `associations/schemas.ts` ↔ `ingest/extract.py:parse_metadata` ↔ `tests/contract-fixtures/ingest-config.json`; the manifest ↔ a new `process-extract-manifest.json` fixture.
- Every enqueue path through the rate ceiling stays in `trigger_run` (its one-function rule).
- `kind` is create-only: it is not in `processUpdateSchema` and `updateProcess` never sets it (the `current_revision` precedent).
- Ledger status mirrors move together: the migration-005 CHECK (now 027), `ingest/repo.py` `STATUS_*`, and `loadgen/sample.py`.
- `_mark`'s all-or-nothing invariant (one statement for a group's rows) applies to the `extracting` transition and to every failure marking.
- Commit messages end with the session's attribution trailer.

---

### Task 1: Migration 027 — `processes.kind`, ledger columns, association-keyed run coalescing

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append after `026_collection_settings_retention_max_items`, before the closing `];` ~line 1407)
- Test: `app/src/__tests__/processes-migration.test.ts` (append a `describe`)

**Interfaces:**
- Produces: column `stac_higher.processes.kind text NOT NULL DEFAULT 'transform'` with CHECK `kind IN ('transform','extractor')`; `stac_higher.ingest_files` status CHECK widened with `'extracting'`, plus columns `reason text`, `extract_run_id uuid`, `source_mtime timestamptz`; `stac_higher.process_runs.association_id uuid` (FK `collection_connections(id) ON DELETE SET NULL`) and partial unique index `process_runs_queued_association_idx ON (process_id, association_id) WHERE status = 'queued' AND association_id IS NOT NULL`.

- [ ] **Step 1: Write the failing test**

Append to `app/src/__tests__/processes-migration.test.ts`:

```ts
describe("migration 027 (extractors — G-6)", () => {
  const sql = migrationEntry("027_extractors");

  it("runs after W-2's 026", () => {
    expect(migrate.indexOf('"027_extractors"')).toBeGreaterThan(
      migrate.indexOf('"026_collection_settings_retention_max_items"'),
    );
  });

  it("adds an immutable-by-omission process kind with a closed CHECK", () => {
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'transform'");
    expect(sql).toContain("CHECK (kind IN ('transform', 'extractor'))");
  });

  it("widens the ledger status set with `extracting` and adds reason/run/mtime", () => {
    expect(sql).toContain("DROP CONSTRAINT IF EXISTS ingest_files_status_check");
    expect(sql).toContain(
      "CHECK (status IN ('seen', 'settled', 'fetching', 'stored', 'extracting', 'itemized', 'failed'))",
    );
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS reason text");
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS extract_run_id uuid");
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS source_mtime timestamptz");
  });

  it("coalesces extractor runs per (process, association), queued rows only", () => {
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS association_id uuid");
    expect(sql).toContain("REFERENCES stac_higher.collection_connections(id) ON DELETE SET NULL");
    expect(sql).toContain("CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_association_idx");
    expect(sql).toContain("WHERE status = 'queued' AND association_id IS NOT NULL");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/processes-migration.test.ts`
Expected: FAIL — `migrationEntry` throws on the unknown name `027_extractors`.

- [ ] **Step 3: Implement**

Append to `MIGRATIONS` in `app/src/lib/db/migrate.ts` (after the 026 entry):

```ts
  {
    // GOES spec §6 + §15 (G-6, extractors).
    //
    // processes.kind — a process is a `transform` (sources → outputs) or an
    // `extractor` (selected on an ingest association, fixes up the draft
    // item before it is catalogued). Immutable after create: the app never
    // puts it in the update path, the `current_revision` precedent.
    //
    // ingest_files — a new `extracting` status between `stored` and
    // `itemized`; `reason` carries the failure text §6.2 promises (the
    // ledger never had one); `extract_run_id` links a row to the run that
    // owns it, so the recovery sweep can tell a live run from a vanished
    // one; `source_mtime` is the listed object modified time DISCOVER used
    // to throw away (I-100) so `file_mtime` can stop meaning "settle time".
    //
    // process_runs.association_id — extractor runs coalesce per
    // (process, association): §6.4 keyed coalescing on source_id, but an
    // extractor has no process_sources rows by §6.1, and enqueue_run only
    // takes the ON CONFLICT path with a source. A second partial unique
    // index is the arbiter, the same shape as 025's.
    name: "027_extractors",
    sql: `
      ALTER TABLE stac_higher.processes
        ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'transform';
      ALTER TABLE stac_higher.processes
        DROP CONSTRAINT IF EXISTS processes_kind_check;
      ALTER TABLE stac_higher.processes
        ADD CONSTRAINT processes_kind_check
        CHECK (kind IN ('transform', 'extractor'));

      ALTER TABLE stac_higher.ingest_files
        DROP CONSTRAINT IF EXISTS ingest_files_status_check;
      ALTER TABLE stac_higher.ingest_files
        ADD CONSTRAINT ingest_files_status_check
        CHECK (status IN ('seen', 'settled', 'fetching', 'stored', 'extracting', 'itemized', 'failed'));
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS reason text;
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS extract_run_id uuid;
      ALTER TABLE stac_higher.ingest_files
        ADD COLUMN IF NOT EXISTS source_mtime timestamptz;

      ALTER TABLE stac_higher.process_runs
        ADD COLUMN IF NOT EXISTS association_id uuid
          REFERENCES stac_higher.collection_connections(id) ON DELETE SET NULL;
      CREATE INDEX IF NOT EXISTS process_runs_association_id_idx
        ON stac_higher.process_runs (association_id);
      CREATE UNIQUE INDEX IF NOT EXISTS process_runs_queued_association_idx
        ON stac_higher.process_runs (process_id, association_id)
        WHERE status = 'queued' AND association_id IS NOT NULL;
    `,
  },
```

- [ ] **Step 4: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/processes-migration.test.ts && npm run check`
Expected: PASS, check green.

```bash
git add app/src/lib/db/migrate.ts app/src/__tests__/processes-migration.test.ts
git commit -m "feat(db): migration 027 — processes.kind, extracting ledger status, association-keyed run coalescing (G-6)"
```

---

### Task 2: App process model — `kind` end to end, and the two 409 guards

**Files:**
- Modify: `app/src/lib/processes/schemas.ts:334-346` (create schema), `app/src/lib/processes/storage.ts:26-29, 58-99, 145-180` (columns, row, API shape, create)
- Modify: `app/src/lib/associations/storage.ts` (append `countAssociationsUsingExtractor`)
- Modify: `app/src/lib/processes/access.ts` (append `refuseIfExtractor`)
- Modify: `app/src/pages/api/processes/index.ts:76-83`, `app/src/pages/api/processes/[id].ts:72-84`, `app/src/pages/api/processes/[id]/sources/index.ts:66-70`, `app/src/pages/api/processes/[id]/outputs/index.ts:65-69`
- Test: `app/src/__tests__/api-processes.test.ts`

**Interfaces:**
- Produces: `PROCESS_KINDS = ["transform", "extractor"] as const`, `type ProcessKind`; `ApiProcess.kind: ProcessKind`; `CreateProcessInput.kind`; `processCreateSchema.kind` (default `"transform"`); `refuseIfExtractor(process: ApiProcess): Response | null` (409 naming the kind); `countAssociationsUsingExtractor(processId: string): Promise<number>`.
- Consumes: migration 027's `kind` column.

- [ ] **Step 1: Write the failing tests**

In `app/src/__tests__/api-processes.test.ts`, add `countAssociationsUsingExtractor: vi.fn(async () => 0)` to the `@/lib/associations/storage` mock — the file does not mock that module today, so add:

```ts
vi.mock("@/lib/associations/storage", () => ({
  countAssociationsUsingExtractor: vi.fn(async () => 0),
}));
```

and import it: `import { countAssociationsUsingExtractor } from "@/lib/associations/storage";`. Find the file's `ApiProcess` fixture factory (grep `current_revision: null` — a helper builds the mocked process row) and add `kind: "transform"` to it. Then append a `describe`:

```ts
describe("process kind (G-6)", () => {
  it("creates a transform by default and passes an explicit extractor through", async () => {
    vi.mocked(createProcess).mockImplementation(async (input) =>
      ({ ...proc(), kind: input.kind }) as ApiProcess,
    );
    const a = await createRoute(
      ctx(authed(["operator"]), { name: "x", group_id: EO }),
    );
    expect(a.status).toBe(201);
    expect(vi.mocked(createProcess).mock.calls[0][0].kind).toBe("transform");

    const b = await createRoute(
      ctx(authed(["operator"]), { name: "y", group_id: EO, kind: "extractor" }),
    );
    expect(b.status).toBe(201);
    expect(vi.mocked(createProcess).mock.calls[1][0].kind).toBe("extractor");
  });

  it("refuses an unknown kind", async () => {
    const res = await createRoute(
      ctx(authed(["operator"]), { name: "z", group_id: EO, kind: "filter" }),
    );
    expect(res.status).toBe(400);
  });

  it("refuses sources and outputs on an extractor with a 409 naming the kind", async () => {
    vi.mocked(getProcess).mockResolvedValue({ ...proc(), kind: "extractor" });
    const src = await createSourceRoute(
      ctx(authed(["operator"]), { collection_id: "c", trigger: { kind: "item_event", item_filter: null } }, { id: PROC }),
    );
    expect(src.status).toBe(409);
    expect((await src.json()).error).toMatch(/extractor/);
    expect(createSource).not.toHaveBeenCalled();

    const out = await createOutputRoute(
      ctx(authed(["operator"]), { collection_id: "c" }, { id: PROC }),
    );
    expect(out.status).toBe(409);
    expect(createOutput).not.toHaveBeenCalled();
  });

  it("refuses deleting an extractor an association still names", async () => {
    vi.mocked(getProcess).mockResolvedValue({ ...proc(), kind: "extractor" });
    vi.mocked(countAssociationsUsingExtractor).mockResolvedValue(2);
    const res = await deleteRoute(ctx(authed(["operator"]), undefined, { id: PROC }));
    expect(res.status).toBe(409);
    expect((await res.json()).error).toMatch(/2 ingest association/);
    expect(softDeleteProcess).not.toHaveBeenCalled();
  });
});
```

Use the file's existing request-context helper and process fixture names (`ctx`, `authed`, `proc`, `PROC`, `EO` are the shapes used there — match whatever the file actually calls them; do not invent a second helper).

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/api-processes.test.ts`
Expected: FAIL — `kind` is stripped by the strict schema / `refuseIfExtractor` does not exist.

- [ ] **Step 3: Implement the schema and storage**

`app/src/lib/processes/schemas.ts` — above `processCreateSchema`:

```ts
/** GOES spec §6.1: a transform is wired to source/output collections; an
 * extractor is selected on an ingest association and fixes up draft items.
 * Create-only — `processUpdateSchema` deliberately lacks it. */
export const PROCESS_KINDS = ["transform", "extractor"] as const;
export type ProcessKind = (typeof PROCESS_KINDS)[number];
```

and in `processCreateSchema` add `kind: z.enum(PROCESS_KINDS).default("transform"),` after `group_id`.

`app/src/lib/processes/storage.ts`:
- `PROCESS_COLUMNS`: add `kind` after `group_id`.
- `ProcessRow` and `ApiProcess`: add `kind: ProcessKind;` (import `type ProcessKind` from `./schemas`). `toApiProcess`: `kind: row.kind,`.
- `CreateProcessInput`: add `kind: ProcessKind;`. In `createProcess` the INSERT becomes
  `(name, description, group_id, kind, enabled, max_runs_per_hour, created_by) VALUES ($1, $2, $3, $4, $5, $6, $7)` with `input.kind` fourth.
- `UpdateProcessInput` / `updateProcess`: **no change** (immutability).

`app/src/lib/associations/storage.ts` — append:

```ts
/** How many live ingest associations name this process as their extractor
 * (GOES spec §6.1). Read before soft-deleting an extractor: a dangling
 * reference would fail every file of those associations at ITEMIZE. */
export async function countAssociationsUsingExtractor(
  processId: string,
): Promise<number> {
  await runMigrations();
  const result = await query<{ n: string }>(
    `SELECT count(*)::text AS n
       FROM stac_higher.collection_connections
      WHERE deleted_at IS NULL AND direction = 'ingest'
        AND config->'metadata'->>'strategy' = 'extractor'
        AND config->'metadata'->'extractor'->>'process_id' = $1`,
    [processId],
  );
  return Number(result.rows[0]?.n ?? 0);
}
```

`app/src/lib/processes/access.ts` — append:

```ts
/** GOES spec §6.1: an extractor has no sources and no outputs — it is
 * selected on an ingest association instead. 409 so the UI can say why. */
export function refuseIfExtractor(process: ApiProcess): Response | null {
  if (process.kind !== "extractor") return null;
  return jsonResponse(409, {
    error:
      "This process is an extractor. Extractors are selected on an ingest " +
      "association's metadata strategy and cannot have trigger sources or " +
      "output collections.",
  });
}
```

- [ ] **Step 4: Implement the routes**

`app/src/pages/api/processes/index.ts` — pass `kind: data.kind,` into `createProcess`.

`app/src/pages/api/processes/[id]/sources/index.ts` POST — right after `if ("response" in loaded) return loaded.response;` add:

```ts
  const refused = refuseIfExtractor(loaded.process);
  if (refused) return refused;
```

(import `refuseIfExtractor` from `@/lib/processes/access`). Same two lines at the same spot in `app/src/pages/api/processes/[id]/outputs/index.ts` POST.

`app/src/pages/api/processes/[id].ts` DELETE — after `loaded` resolves and before `softDeleteProcess`:

```ts
    if (loaded.process.kind === "extractor") {
      const users = await countAssociationsUsingExtractor(loaded.process.id);
      if (users > 0) {
        return jsonResponse(409, {
          error:
            `This extractor is named by ${users} ingest association${users === 1 ? "" : "s"}. ` +
            "Switch those associations to another metadata strategy first.",
        });
      }
    }
```

(import `countAssociationsUsingExtractor` from `@/lib/associations/storage`).

- [ ] **Step 5: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/api-processes.test.ts src/__tests__/authz-processes-gate.test.ts && npm run check`
Expected: PASS.

```bash
git add app/src/lib/processes app/src/lib/associations/storage.ts app/src/pages/api/processes app/src/__tests__/api-processes.test.ts
git commit -m "feat(processes): create-only kind, extractor refuses sources/outputs and guarded delete (G-6)"
```

---

### Task 3: Association contract — `metadata.strategy: "extractor"` in Zod, the fixture, and the routes

**Files:**
- Modify: `app/src/lib/associations/schemas.ts:41-66` (`metadataSchema`)
- Modify: `tests/contract-fixtures/ingest-config.json` (append four cases)
- Modify: `app/src/lib/associations/access.ts:124-137` (`resolveUsableConnection` returns `group_id` too; append `refuseUnusableExtractor`)
- Modify: `app/src/pages/api/collections/[id]/connections/index.ts:111-127`, `app/src/pages/api/collections/[id]/connections/[assocId].ts:66-83`
- Test: `app/src/__tests__/associations-schemas.test.ts`, `app/src/__tests__/api-associations.test.ts`, `app/src/__tests__/contract-fixtures.test.ts` (runs unchanged; the new cases must pass)

**Interfaces:**
- Produces: `metadataSchema` accepts `{ strategy: "extractor", extractor: { process_id: uuid } }` and refuses `extractor` without `extractor.process_id` or `extractor` on another strategy; `resolveUsableConnection` returns `{ protocol, group_id }`; `refuseUnusableExtractor(config, connectionGroupId): Promise<Response | null>` (400 when the process is missing/not visible/wrong group, 409 when `kind !== "extractor"`).
- Consumes: `getProcess` + `ApiProcess.kind` from Task 2.

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/associations-schemas.test.ts` — append:

```ts
describe("metadata.strategy extractor (G-6)", () => {
  const PROC = "5c9f1c2e-0000-4000-8000-0000000000e1";
  it("accepts an extractor with a process id", () => {
    const r = ingestConfigSchema.safeParse({
      source_path: "/out",
      metadata: { strategy: "extractor", extractor: { process_id: PROC } },
    });
    expect(r.success).toBe(true);
    expect(r.success && r.data.metadata.extractor?.process_id).toBe(PROC);
  });
  it("refuses extractor without a process id", () => {
    const r = ingestConfigSchema.safeParse({
      source_path: "/out",
      metadata: { strategy: "extractor" },
    });
    expect(r.success).toBe(false);
  });
  it("refuses a non-uuid process id", () => {
    const r = ingestConfigSchema.safeParse({
      source_path: "/out",
      metadata: { strategy: "extractor", extractor: { process_id: "goes" } },
    });
    expect(r.success).toBe(false);
  });
  it("refuses an extractor block on another strategy", () => {
    const r = ingestConfigSchema.safeParse({
      source_path: "/out",
      metadata: { strategy: "defaults_only", extractor: { process_id: PROC } },
    });
    expect(r.success).toBe(false);
  });
});
```

`app/src/__tests__/api-associations.test.ts` — add `vi.mock("@/lib/processes/storage", () => ({ getProcess: vi.fn() }))`, import `getProcess`, and append:

```ts
describe("extractor strategy on create/update (G-6)", () => {
  const PROC = "5c9f1c2e-0000-4000-8000-0000000000e1";
  const extractorConfig = {
    source_path: "/out",
    metadata: { strategy: "extractor", extractor: { process_id: PROC } },
  };
  const s3Conn = { ...conn, protocol: "s3", group_id: EO } as ApiConnection;

  beforeEach(() => {
    vi.mocked(getConnection).mockResolvedValue(s3Conn);
    vi.mocked(getCollectionSettings).mockResolvedValue(makeCollectionSettings({ collectionId: COLLECTION }));
  });

  it("accepts an extractor the connection's group owns", async () => {
    vi.mocked(getProcess).mockResolvedValue({ id: PROC, kind: "extractor", group_id: EO } as never);
    vi.mocked(createAssociation).mockResolvedValue(assoc);
    const res = await createRoute(
      ctx(authed(["operator"]), { connection_id: CONN_ID, direction: "ingest", config: extractorConfig }, { id: COLLECTION }),
    );
    expect(res.status).toBe(201);
  });

  it("400s when the process is missing or in another group", async () => {
    vi.mocked(getProcess).mockResolvedValue({ id: PROC, kind: "extractor", group_id: "other" } as never);
    const res = await createRoute(
      ctx(authed(["operator"]), { connection_id: CONN_ID, direction: "ingest", config: extractorConfig }, { id: COLLECTION }),
    );
    expect(res.status).toBe(400);
    expect(createAssociation).not.toHaveBeenCalled();
  });

  it("409s when the named process is a transform", async () => {
    vi.mocked(getProcess).mockResolvedValue({ id: PROC, kind: "transform", group_id: EO } as never);
    const res = await createRoute(
      ctx(authed(["operator"]), { connection_id: CONN_ID, direction: "ingest", config: extractorConfig }, { id: COLLECTION }),
    );
    expect(res.status).toBe(409);
  });

  it("re-checks on update", async () => {
    vi.mocked(getAssociation).mockResolvedValue(assoc);
    vi.mocked(getProcess).mockResolvedValue({ id: PROC, kind: "transform", group_id: EO } as never);
    const res = await putRoute(
      ctx(authed(["operator"]), { config: extractorConfig }, { id: COLLECTION, assocId: ASSOC_ID }),
    );
    expect(res.status).toBe(409);
    expect(updateAssociation).not.toHaveBeenCalled();
  });
});
```

(Match the file's actual helper names for building a request context and the `conn` fixture — read the top 150 lines of the test before writing.)

`tests/contract-fixtures/ingest-config.json` — append to `cases`:

```json
{
  "name": "extractor strategy",
  "config": {
    "source_path": "ABI-L2-MCMIPC/",
    "storage_mode": "reference",
    "metadata": {
      "strategy": "extractor",
      "extractor": { "process_id": "5c9f1c2e-0000-4000-8000-0000000000e1" }
    }
  },
  "app": "accept",
  "pipeline": "accept"
},
{
  "name": "extractor strategy without a process id",
  "config": { "source_path": "/out", "metadata": { "strategy": "extractor" } },
  "app": "reject",
  "pipeline": "reject"
},
{
  "name": "extractor block on another strategy",
  "config": {
    "source_path": "/out",
    "metadata": {
      "strategy": "defaults_only",
      "extractor": { "process_id": "5c9f1c2e-0000-4000-8000-0000000000e1" }
    }
  },
  "app": "reject",
  "pipeline": "accept"
},
{
  "name": "extractor with a non-uuid process id (writer strict, reader lenient)",
  "config": {
    "source_path": "/out",
    "metadata": { "strategy": "extractor", "extractor": { "process_id": "goes" } }
  },
  "app": "reject",
  "pipeline": "accept"
}
```

The pipeline verdicts here are what Task 6's `parse_metadata` implements: it needs a non-empty `process_id` for `extractor` and ignores an `extractor` block on other strategies (lenient reader — the README's asymmetry).

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/associations-schemas.test.ts src/__tests__/api-associations.test.ts src/__tests__/contract-fixtures.test.ts`
Expected: FAIL — the enum lacks `extractor`; the `extractor` key is refused by `.strict()`.

- [ ] **Step 3: Implement the schema**

`metadataSchema` in `app/src/lib/associations/schemas.ts` becomes:

```ts
export const metadataSchema = z
  .object({
    strategy: z
      .enum(["raster_auto", "sidecar", "defaults_only", "extractor"])
      .default("raster_auto"),
    sidecar: z
      .object({
        pattern: z.string().min(1),
        parser: z.enum(["generic_xml", "json"]).default("generic_xml"),
      })
      .strict()
      .optional(),
    // GOES spec §6: the operator-authored process that fixes up each draft
    // item. Required with `strategy: extractor`, refused otherwise — the
    // route checks the process exists, is kind=extractor and is owned by
    // the connection's group.
    extractor: z
      .object({ process_id: z.string().uuid() })
      .strict()
      .optional(),
    // Collection-level fallbacks applied when extraction leaves a field unset.
    defaults: z
      .object({
        datetime: z.string().min(1).optional(),
        geometry: z.enum(["collection"]).optional(),
      })
      .strict()
      .default({}),
  })
  .strict()
  .superRefine((m, ctx) => {
    if (m.strategy === "extractor" && !m.extractor) {
      ctx.addIssue({
        code: "custom",
        path: ["extractor"],
        message: "strategy 'extractor' needs extractor.process_id",
      });
    }
    if (m.strategy !== "extractor" && m.extractor) {
      ctx.addIssue({
        code: "custom",
        path: ["extractor"],
        message: "extractor.process_id is only valid with strategy 'extractor'",
      });
    }
  });
```

Keep the existing `defaults.geometry` comment. `ingestConfigSchema`'s `metadata: metadataSchema.default(() => metadataSchema.parse({}))` line is unchanged.

- [ ] **Step 4: Implement the access helper and routes**

`app/src/lib/associations/access.ts`: change `resolveUsableConnection`'s return type to `{ protocol: string; group_id: string } | { response: Response }` and return `{ protocol: connection.protocol, group_id: connection.group_id }`. Append:

```ts
/**
 * GOES spec §6.1 / §15: an ingest association may name an extractor only
 * when the process exists, is `kind = 'extractor'`, and belongs to the
 * association's group — the CONNECTION's group, the same notion
 * `resolveUsableConnection` applies. A missing or foreign process is a 400
 * on the body field (never an oracle for which ids exist); a transform is
 * a 409 naming the kind. Returns null when the config names no extractor.
 */
export async function refuseUnusableExtractor(
  config: unknown,
  connectionGroupId: string,
): Promise<Response | null> {
  const metadata = (config as { metadata?: { strategy?: string; extractor?: { process_id?: string } } } | null)?.metadata;
  if (metadata?.strategy !== "extractor") return null;
  const processId = metadata.extractor?.process_id;
  const process = processId ? await getProcess(processId) : null;
  if (!process || process.group_id !== connectionGroupId) {
    return jsonResponse(400, {
      error:
        "metadata.extractor.process_id must name an extractor process owned by " +
        "the connection's group",
    });
  }
  if (process.kind !== "extractor") {
    return jsonResponse(409, {
      error: `Process '${process.name}' is a ${process.kind}, not an extractor`,
    });
  }
  return null;
}
```

(import `getProcess` from `@/lib/processes/storage`.)

`app/src/pages/api/collections/[id]/connections/index.ts` POST — after the `storage_mode 'reference'` check:

```ts
    if (data.direction === "ingest") {
      const refused = await refuseUnusableExtractor(data.config, connection.group_id);
      if (refused) return refused;
    }
```

`app/src/pages/api/collections/[id]/connections/[assocId].ts` PUT — the existing block only resolves the connection for `reference`; restructure so the connection is resolved whenever `data.config` is present and the direction is `ingest`:

```ts
    if (data.config && existing.direction === "ingest" && locals.auth?.authenticated) {
      const connection = await resolveUsableConnection(
        locals.auth.identity,
        existing.connection_id,
      );
      if ("response" in connection) return connection.response;
      if (
        "storage_mode" in data.config &&
        data.config.storage_mode === "reference" &&
        connection.protocol !== "s3"
      ) {
        return jsonResponse(400, {
          error: "storage_mode 'reference' requires an object-store (s3) connection",
        });
      }
      const refused = await refuseUnusableExtractor(data.config, connection.group_id);
      if (refused) return refused;
    }
```

Check the existing `api-associations.test.ts` cases for PUT still pass — one of them may mock `getConnection` to return nothing for a non-reference update; if so, that test now needs `getConnection` to resolve a connection (the PUT resolves it for every ingest config update now). Fix the test's mock, not the route.

- [ ] **Step 5: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/associations-schemas.test.ts src/__tests__/api-associations.test.ts src/__tests__/contract-fixtures.test.ts && npm run check`
Expected: PASS.

```bash
git add app/src/lib/associations tests/contract-fixtures/ingest-config.json app/src/pages/api/collections app/src/__tests__/associations-schemas.test.ts app/src/__tests__/api-associations.test.ts
git commit -m "feat(associations): metadata.strategy extractor — schema, fixture cases, group/kind checks on the routes (G-6)"
```

---

### Task 4: Graph — the display-only `extractor` edge, process → collection

**Files:**
- Modify: `app/src/lib/graph/edges.ts:32-36` (`GraphEdgeKind`)
- Modify: `app/src/lib/graph/storage.ts:40-102` (`loadGraphEdges`)
- Modify: `app/src/components/collections/LineagePanel.tsx:62-77`, `app/src/components/monitoring/PipelineGraph.tsx:160-231`
- Test: `app/src/__tests__/graph-cycles.test.ts`

**Interfaces:**
- Produces: `GraphEdgeKind` gains `"extractor"`; `loadGraphEdges()` emits `{ from: processNode(process_id), to: collectionNode(collection_id), kind: "extractor", id: association_id }` for every enabled ingest association with `strategy = 'extractor'` naming an enabled, live process. Not in `TRAVERSABLE_KINDS`.

- [ ] **Step 1: Write the failing test**

Append to `app/src/__tests__/graph-cycles.test.ts`:

```ts
describe("extractor edges (G-6)", () => {
  it("are display-only: the cycle check never walks them", () => {
    const edges: GraphEdge[] = [
      { from: processNode("ex"), to: collectionNode("src"), kind: "extractor", id: "a1" },
      { from: collectionNode("src"), to: processNode("p"), kind: "process_source", id: "s1" },
    ];
    // p → ex would only close a loop THROUGH the extractor edge, which is
    // not traversable, so attaching `ex`'s "output" to src is not a cycle.
    expect(wouldCycle(edges, processNode("p"), collectionNode("src"))).toBeNull();
    expect(findPath(edges, collectionNode("src"), processNode("ex"))).toBeNull();
  });
});
```

(Import `GraphEdge`, `findPath`, `wouldCycle`, `processNode`, `collectionNode` from `@/lib/graph/edges` the way the file already does.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/graph-cycles.test.ts`
Expected: FAIL to typecheck (`"extractor"` is not a `GraphEdgeKind`) — vitest reports the TS error or the assertion; either is the failing signal.

- [ ] **Step 3: Implement**

`edges.ts`: add `| "extractor"` to `GraphEdgeKind`, and extend the `TRAVERSABLE_KINDS` comment with one sentence: "An `extractor` edge (GOES spec §6.6 / §15) is display-only too — it produces no `process_output`, so it cannot close a collection↔process loop."

`storage.ts` `loadGraphEdges`: add a fourth query to the `Promise.all`:

```ts
    query<ExtractorEdgeRow>(
      `SELECT cc.id, cc.collection_id,
              cc.config->'metadata'->'extractor'->>'process_id' AS process_id
         FROM stac_higher.collection_connections cc
         JOIN stac_higher.connections c ON c.id = cc.connection_id
         JOIN stac_higher.processes p
           ON p.id::text = cc.config->'metadata'->'extractor'->>'process_id'
        WHERE cc.direction = 'ingest' AND cc.enabled AND c.enabled
          AND cc.deleted_at IS NULL AND c.deleted_at IS NULL
          AND p.enabled AND p.deleted_at IS NULL
          AND cc.config->'metadata'->>'strategy' = 'extractor'`,
    ),
```

with `interface ExtractorEdgeRow { id: string; collection_id: string; process_id: string; }`, destructured as `extractors`, and after the outputs loop:

```ts
  for (const row of extractors.rows) {
    // GOES spec §15: drawn from the extractor PROCESS to the collection it
    // fixes items for, so the process appears in the picture and the
    // collection's lineage. Same association id as its `ingest` twin.
    edges.push({
      from: processNode(row.process_id),
      to: collectionNode(row.collection_id),
      kind: "extractor",
      id: row.id,
    });
  }
```

`LineagePanel.tsx` `decorate`: an extractor edge shares its association id with the ingest edge that already carries the flow strip, so it must not draw a second one:

```ts
        hasHistory: edge.kind !== "process_output" && edge.kind !== "extractor",
```

and `key={edge.id}` on the two `.map` renders becomes `key={`${edge.kind}:${edge.id}`}` (the ingest and extractor edges share an id).

`PipelineGraph.tsx` `GraphContent`: no column change — a process with only an extractor edge already lands in "Processes" through `degree`, and the collection stays a source product because `derived` keys on `process_output`. Add one comment above `const derived = …`: `// An \`extractor\` edge does not make a collection derived: the extractor fixes up items ingested INTO it.`

- [ ] **Step 4: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/graph-cycles.test.ts src/__tests__/api-monitoring-graph.test.ts && npm run check`
Expected: PASS.

```bash
git add app/src/lib/graph app/src/components/collections/LineagePanel.tsx app/src/components/monitoring/PipelineGraph.tsx app/src/__tests__/graph-cycles.test.ts
git commit -m "feat(graph): display-only extractor edge, process -> collection (G-6)"
```

---

### Task 5: UI — kind at create, badge, the extractor detail card, the association picker

**Files:**
- Modify: `app/src/components/processes/ProcessesPage.tsx:107-143, 231-325`
- Modify: `app/src/components/processes/ProcessDetailPage.tsx:237-256, 958-977` (+ a new `ExtractorCard`)
- Modify: `app/src/components/collections/IngestFormDialog.tsx:37-75, 91-141, 168-180, 397-414`
- Test: `app/src/__tests__/process-code-card.test.tsx`, create `app/src/__tests__/ingest-form-extractor.test.tsx`

**Interfaces:**
- Consumes: `Process.kind` (Task 2), `useProcesses()` from `@/lib/processes/queries`, `metadataSchema` (Task 3).
- Produces: `CodeCard` prop `kind: ProcessKind` (timeout default 120 for extractors, 900 otherwise); `IngestFormState.metadataStrategy` gains `"extractor"` and `extractorProcessId: string`.

- [ ] **Step 1: Write the failing tests**

`app/src/__tests__/process-code-card.test.tsx` — read how it renders `CodeCard` and asserts the deploy payload; add:

```tsx
it("defaults an extractor's timeout to 120 s and a transform's to 900 s", async () => {
  // render with kind="extractor" (add the prop to the existing render helper)
  // and click Deploy; assert the mutation's runtime.timeout_seconds === 120.
  // Repeat with kind="transform"; assert 900.
});
```

Write it against the file's existing render/mutation-mock pattern (the mocked `useDeployRevision` records `mutateAsync` calls — assert on `input.runtime.timeout_seconds`).

Create `app/src/__tests__/ingest-form-extractor.test.tsx`, modelled on `ingest-form-window.test.tsx` (copy its mocks, `s3Connection()`, `ingestAssociation()` helpers and the Radix `beforeAll`), plus:

```tsx
vi.mock("@/lib/processes/queries", () => ({
  useProcesses: () => ({
    data: [
      { id: "5c9f1c2e-0000-4000-8000-0000000000e1", name: "goes-abi-metadata", kind: "extractor", group_id: "g1" },
      { id: "5c9f1c2e-0000-4000-8000-0000000000e2", name: "other-group-extractor", kind: "extractor", group_id: "g2" },
      { id: "5c9f1c2e-0000-4000-8000-0000000000e3", name: "a-transform", kind: "transform", group_id: "g1" },
    ],
  }),
}));

it("offers only the connection group's extractors and emits extractor.process_id", async () => {
  render(<IngestFormDialog collectionId={COLLECTION} open editing={null} onOpenChange={() => {}} connections={[s3Connection()]} />);
  // pick the connection, fill a source path, choose strategy "extractor"
  // (open the Metadata select, click the "extractor process" option),
  // open the "Extractor process" select: expect "goes-abi-metadata" listed,
  // "other-group-extractor" and "a-transform" NOT listed; pick it; submit.
  expect(createMutate).toHaveBeenCalledTimes(1);
  const payload = createMutate.mock.calls[0][0];
  expect(payload.config.metadata).toEqual({
    strategy: "extractor",
    extractor: { process_id: "5c9f1c2e-0000-4000-8000-0000000000e1" },
    defaults: {},
  });
});

it("refuses to submit an extractor strategy with no process picked", async () => {
  // same setup, strategy extractor, no pick, submit → createMutate not called
});

it("seeds the picker from a stored extractor config", () => {
  const a = ingestAssociation({
    source_path: "x/", metadata: { strategy: "extractor", extractor: { process_id: "5c9f1c2e-0000-4000-8000-0000000000e1" } },
  });
  render(<IngestFormDialog collectionId={COLLECTION} open editing={a} onOpenChange={() => {}} connections={[s3Connection()]} />);
  expect(screen.getByRole("combobox", { name: "Extractor process" })).toHaveTextContent("goes-abi-metadata");
});
```

Fill the interaction steps with the same `fireEvent`/`screen.getByRole` idiom `ingest-form-window.test.tsx` uses for its Select fields.

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/process-code-card.test.tsx src/__tests__/ingest-form-extractor.test.tsx`
Expected: FAIL — no `kind` prop; no "extractor process" option.

- [ ] **Step 3: Implement the process pages**

`ProcessesPage.tsx`:
- `ProcessCard` header row (`<div className="flex flex-wrap items-center gap-2">` around the name link): add `{process.kind === "extractor" && <Badge variant="outline">extractor</Badge>}` after the link.
- `CreateProcessDialog`: add `const [kind, setKind] = useState<ProcessKind>("transform");`, import `Select, SelectContent, SelectItem, SelectTrigger, SelectValue` from `@stac-higher/shared` and `type ProcessKind, PROCESS_KINDS` from `@/lib/processes/schemas`. In the form grid, after the group field:

```tsx
            <div className="grid gap-2">
              <Label htmlFor="process-kind">Kind</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as ProcessKind)}>
                <SelectTrigger id="process-kind" aria-label="Kind">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="transform">transform — sources → outputs</SelectItem>
                  <SelectItem value="extractor">extractor — fixes up items an ingest source brings in</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[12px] text-muted-foreground">
                Cannot be changed after creation.
              </p>
            </div>
```

and the payload becomes `{ name, description, group_id: groupId, kind, enabled: true, max_runs_per_hour: kind === "extractor" ? 600 : 60 }` (GOES spec §6.5).
- Page copy "User-defined transforms…" → "User-defined transforms and extractors…".

`ProcessDetailPage.tsx`:
- `CodeCard` gains `kind: ProcessKind` in its props; `useState(kind === "extractor" ? 120 : 900)` for `timeoutSeconds` (comment: GOES spec §6.5).
- New component above `ProcessDetailContent`:

```tsx
function ExtractorCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Extractor</CardTitle>
        <CardDescription>
          This process fixes up items as an ingest source brings them in. It
          is selected on an ingest association's metadata strategy (a
          collection's Data flow tab) and has no trigger sources or output
          collections of its own. Each run receives the draft items in its
          input manifest (<code className="tech">kind: "extract"</code>) and
          writes one <code className="tech">{"{item_id}.json"}</code> per item
          back — id, collection and asset hrefs unchanged.
        </CardDescription>
      </CardHeader>
    </Card>
  );
}
```

- In `ProcessDetailContent`, replace the two lines rendering `SourcesCard`/`OutputsCard` with:

```tsx
          {process.kind === "extractor" ? (
            <ExtractorCard />
          ) : (
            <>
              <SourcesCard id={process.id} canMutate={canMutate} />
              <OutputsCard id={process.id} canMutate={canMutate} />
            </>
          )}
```

and pass `kind={process.kind}` to `<CodeCard>`. Add `{process.kind === "extractor" && <Badge variant="outline">extractor</Badge>}` next to `<DeployState>` in the header.

- [ ] **Step 4: Implement the ingest form**

`IngestFormDialog.tsx`:
- `IngestFormState.metadataStrategy: "raster_auto" | "sidecar" | "defaults_only" | "extractor"`; add `extractorProcessId: string;` (`""` in `emptyForm()`).
- `formFromAssociation`: `extractorProcessId: c.metadata.extractor?.process_id ?? ""`.
- `buildConfig`: `metadata: { strategy: form.metadataStrategy, ...(form.metadataStrategy === "extractor" && form.extractorProcessId ? { extractor: { process_id: form.extractorProcessId } } : {}) }`.
- `submit` guards: after the move-path guard add
  `if (form.metadataStrategy === "extractor" && !form.extractorProcessId) { toast.error("Pick an extractor process"); return; }`.
- Data: `const { data: processes } = useProcesses();` (import from `@/lib/processes/queries`). The association's group is the selected connection's: `const connectionGroup = connections.find((c) => c.id === form.connectionId)?.group_id ?? null;` and `const extractors = (processes ?? []).filter((p) => p.kind === "extractor" && p.group_id === connectionGroup);` (GOES spec §15: client-side filter over the already group-scoped list).
- Metadata `<Select>`: add `<SelectItem value="extractor">extractor process</SelectItem>`.
- Directly below the Grouping/Metadata grid, the picker, using the same conditional idiom as the move-path field:

```tsx
          {form.metadataStrategy === "extractor" && (
            <div className="space-y-1.5">
              <Label htmlFor="df-extractor">Extractor process</Label>
              <Select
                value={form.extractorProcessId}
                onValueChange={(v) => update({ extractorProcessId: v })}
              >
                <SelectTrigger id="df-extractor" aria-label="Extractor process">
                  <SelectValue placeholder={extractors.length ? "Pick an extractor" : "No extractor in this connection's group"} />
                </SelectTrigger>
                <SelectContent>
                  {extractors.map((p) => (
                    <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-[12px] text-muted-foreground">
                Each file's draft item is handed to this process before it is
                catalogued. Reference-mode files are staged for the run.
              </p>
            </div>
          )}
```

- [ ] **Step 5: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/process-code-card.test.tsx src/__tests__/ingest-form-extractor.test.tsx src/__tests__/ingest-form-window.test.tsx && npm run check`
Expected: PASS.

```bash
git add app/src/components/processes app/src/components/collections/IngestFormDialog.tsx app/src/__tests__/process-code-card.test.tsx app/src/__tests__/ingest-form-extractor.test.tsx
git commit -m "feat(ui): process kind at create + badge, extractor detail card, extractor picker on the ingest form (G-6)"
```

---

### Task 6: Pipeline ledger + contract — `extracting`, `reason`, `extract_run_id`, `source_mtime` (I-100), `parse_metadata`

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/repo.py:30-36, 55-70, 108-118, 160-166, 198-236, 314-330, 401-413` (+ new methods)
- Modify: `services/pipeline/src/pipeline/ingest/extract.py:85-113, 139-152` (`MetadataConfig`, `parse_metadata`, `resolve_datetime` unchanged but fed by `_member`)
- Modify: `services/pipeline/src/pipeline/ingest/itemize.py:98-105` (`_member` uses `source_mtime`)
- Modify: `services/pipeline/src/pipeline/ingest/discover.py:249-262, 293-321` (persist `entry.mtime`)
- Modify: `services/pipeline/src/pipeline/loadgen/sample.py:80-96` (add `ingest_files_extracting`)
- Modify: `services/pipeline/tests/_ingest_fake.py`
- Test: `services/pipeline/tests/test_ingest_extract.py`, `services/pipeline/tests/test_ingest_discover.py`, `services/pipeline/tests/test_ingest_recovery.py`, `services/pipeline/tests/test_contract_fixtures.py` (runs unchanged)

**Interfaces:**
- Produces: `STATUS_EXTRACTING = "extracting"`; `LedgerEntry.reason: str | None`, `.extract_run_id: str | None`, `.source_mtime: dt.datetime | None`; `insert_ledger_version(..., source_mtime: dt.datetime | None = None)`; `set_ledger_status_many(entry_ids, *, status, item_id=None, reason=None)`; new `get_ledger_entries(entry_ids) -> list[LedgerEntry]`; new `set_extract_run(entry_ids, run_id)`; new `sweep_stuck_extracting(older_than_seconds) -> int`; `MetadataConfig.extractor_process_id: str | None`; `parse_metadata` accepts `"extractor"`.
- Consumes: migration 027 columns.

- [ ] **Step 1: Write the failing tests**

`tests/test_ingest_extract.py` — append:

```python
def test_parse_metadata_extractor_needs_a_process_id():
    from pipeline.ingest.extract import ExtractError, parse_metadata

    cfg = parse_metadata({"strategy": "extractor", "extractor": {"process_id": "p1"}})
    assert cfg.strategy == "extractor"
    assert cfg.extractor_process_id == "p1"
    with pytest.raises(ExtractError, match="extractor.process_id"):
        parse_metadata({"strategy": "extractor"})
    # lenient reader: an extractor block on another strategy is ignored
    cfg = parse_metadata({"strategy": "defaults_only", "extractor": {"process_id": "p1"}})
    assert cfg.extractor_process_id is None
```

`tests/test_ingest_discover.py` — find a test that inserts a first-seen file through `discover_stage` with a `FileEntry` carrying `mtime` (grep `mtime=`), and add:

```python
async def test_discover_persists_the_listed_mtime(...):
    # same setup as the neighbouring first-sight test, with
    # FileEntry(path=..., size=1, mtime=1_700_000_000.0)
    ...
    row = await repo.get_latest_ledger(assoc.id, "a.tif")
    assert row.source_mtime == dt.datetime.fromtimestamp(1_700_000_000.0, tz=dt.UTC)
```

`tests/test_ingest_recovery.py` — append:

```python
async def test_sweep_stuck_extracting_fails_rows_whose_run_is_gone():
    from pipeline.ingest.repo import STATUS_EXTRACTING, STATUS_FAILED

    repo = FakeIngestRepo()
    assoc = make_association({"source_path": "/o"})
    live = await repo.insert_ledger_version(assoc.id, "live.nc", version=1, status=STATUS_EXTRACTING, size=1, fingerprint="f")
    gone = await repo.insert_ledger_version(assoc.id, "gone.nc", version=1, status=STATUS_EXTRACTING, size=1, fingerprint="f")
    never = await repo.insert_ledger_version(assoc.id, "never.nc", version=1, status=STATUS_EXTRACTING, size=1, fingerprint="f")
    await repo.set_extract_run([live], "run-live")
    await repo.set_extract_run([gone], "run-gone")
    repo.run_statuses = {"run-live": "running"}  # run-gone is absent; never has no run
    repo.now = repo.now + dt.timedelta(hours=1)

    failed = await repo.sweep_stuck_extracting(older_than_seconds=1800)

    assert failed == 2
    assert repo.rows[live].status == STATUS_EXTRACTING
    assert repo.rows[gone].status == STATUS_FAILED and "run" in repo.rows[gone].reason
    assert repo.rows[never].status == STATUS_FAILED
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_extract.py tests/test_ingest_discover.py tests/test_ingest_recovery.py -q`
Expected: FAIL — unknown strategy / no `source_mtime` / no `sweep_stuck_extracting`.

- [ ] **Step 3: Implement the repo**

`ingest/repo.py`:
- Constants: add `STATUS_EXTRACTING = "extracting"` between `STATUS_STORED` and `STATUS_ITEMIZED`; the comment becomes "mirrors the CHECK constraint (migration 005, widened by 027)".
- `LedgerEntry`: add `reason: str | None = None`, `extract_run_id: str | None = None`, `source_mtime: dt.datetime | None = None` after `source_href`.
- `_LEDGER_COLUMNS`: append `, reason, extract_run_id, source_mtime`; `_to_ledger_entry` unpacks the three extra fields and passes them through (`extract_run_id=str(extract_run_id) if extract_run_id else None`).
- `_LEDGER_MUTABLE`: add `"reason"`.
- ABC + Pg `insert_ledger_version`: new kwarg `source_mtime: dt.datetime | None = None`; the INSERT gains the column.
- ABC + Pg `set_ledger_status_many`: new kwarg `reason: str | None = None`; SQL `SET status = %s, item_id = %s, reason = %s, updated_at = now()`. Docstring: "``reason`` is the failure text an operator reads (G-6); pass None on success so a stale reason does not outlive the failure."
- New ABC + Pg methods:

```python
    @abc.abstractmethod
    async def get_ledger_entries(self, entry_ids: Sequence[str]) -> list[LedgerEntry]:
        """The rows for ``entry_ids`` (any status). The extract finalize
        branch re-reads its batch's rows by id — the same idempotent guard
        ITEMIZE applies by source path."""

    @abc.abstractmethod
    async def set_extract_run(self, entry_ids: Sequence[str], run_id: str) -> None:
        """Stamp the extractor run that owns these ``extracting`` rows, so the
        recovery sweep can tell a live run from a vanished one (G-6)."""

    @abc.abstractmethod
    async def sweep_stuck_extracting(self, older_than_seconds: int) -> int:
        """Extract-stall recovery (G-6): an ``extracting`` row older than the
        threshold whose run was never stamped, or whose run row is gone or no
        longer open (not queued/running/failed), is failed with a reason. The
        failed-retry sweep then re-drives it like any failed file. Returns the
        number of rows failed."""
```

Pg implementations:

```python
    async def get_ledger_entries(  # pragma: no cover
        self, entry_ids: Sequence[str]
    ) -> list[LedgerEntry]:
        ids = list(entry_ids)
        if not ids:
            return []
        async with await self._connect() as conn:
            cur = await conn.execute(
                f"SELECT {_LEDGER_COLUMNS} FROM stac_higher.ingest_files"
                " WHERE id = ANY(%s::uuid[]) ORDER BY created_at",
                (ids,),
            )
            rows = await cur.fetchall()
        return [_to_ledger_entry(r) for r in rows]

    async def set_extract_run(  # pragma: no cover
        self, entry_ids: Sequence[str], run_id: str
    ) -> None:
        if not entry_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.ingest_files SET extract_run_id = %s"
                " WHERE id = ANY(%s::uuid[])",
                (run_id, list(entry_ids)),
            )
            await conn.commit()

    async def sweep_stuck_extracting(  # pragma: no cover
        self, older_than_seconds: int
    ) -> int:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "UPDATE stac_higher.ingest_files f"
                "   SET status = 'failed',"
                "       reason = CASE WHEN f.extract_run_id IS NULL"
                "                     THEN 'extractor run was never queued'"
                "                     ELSE 'extractor run ' || f.extract_run_id || ' is gone or closed'"
                "                END,"
                "       updated_at = now()"
                " WHERE f.status = 'extracting'"
                "   AND f.updated_at < now() - make_interval(secs => %s)"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM stac_higher.process_runs r"
                "      WHERE r.id = f.extract_run_id"
                "        AND r.status IN ('queued', 'running', 'failed'))",
                (older_than_seconds,),
            )
            count = cur.rowcount or 0
            await conn.commit()
        return count
```

(A `succeeded` run whose finalize job was lost is "closed" here on purpose: after the threshold its rows fail and the failed-retry sweep re-drives the file; the finalize branch's own idempotent guard means a late finalize finds no `extracting` rows and does nothing.)

`tests/_ingest_fake.py` `FakeIngestRepo`: add field `run_statuses: dict[str, str] = field(default_factory=dict)`; `insert_ledger_version` takes and stores `source_mtime`; `set_ledger_status_many` takes `reason` and sets `row.reason = reason`; implement the three new methods over `self.rows` (`sweep_stuck_extracting`: rows with `status == "extracting"`, `updated_at < cutoff`, and `extract_run_id is None or run_statuses.get(extract_run_id) not in ("queued", "running", "failed")` → `status = "failed"`, `reason` as in the SQL, `updated_at = now`).

- [ ] **Step 4: Implement the contract, the mtime, the sample**

`ingest/extract.py`:
- `MetadataConfig`: add `extractor_process_id: str | None` after `default_geometry` with the comment `#: GOES spec §6: the process that fixes up the draft item; set only for strategy "extractor".`
- `parse_metadata`:

```python
    strategy = str(raw.get("strategy", "raster_auto"))
    if strategy not in ("raster_auto", "sidecar", "defaults_only", "extractor"):
        raise ExtractError(f"unknown metadata.strategy {strategy!r}")
    extractor_process_id: str | None = None
    if strategy == "extractor":
        extractor_process_id = str((raw.get("extractor") or {}).get("process_id") or "") or None
        if extractor_process_id is None:
            raise ExtractError("metadata.strategy 'extractor' needs extractor.process_id")
```

and pass `extractor_process_id=extractor_process_id` into the dataclass.

`ingest/itemize.py` `_member`: `observed_at=entry.source_mtime or entry.updated_at,` with the comment `# I-100: the listed object mtime when DISCOVER recorded one, else the settle time.`

`ingest/discover.py` `_reconcile`: every `insert_ledger_version(...)` call (first sight, re-ingest, failed-retry) gains `source_mtime=_mtime(entry)` where, at module level:

```python
def _mtime(entry: FileEntry) -> dt.datetime | None:
    """The listed modified time as an aware datetime (I-100), or None."""
    if entry.mtime is None:
        return None
    return dt.datetime.fromtimestamp(entry.mtime, tz=dt.UTC)
```

(import `datetime as dt` if the module lacks it). The `seen → settled` transition through `set_ledger_fields` does not need it — the row already carries it from insert.

`loadgen/sample.py` `TABLE_QUERIES`: add after `ingest_files_stored`:

```python
    "ingest_files_extracting": (
        # G-6: files parked while their extractor run executes.
        "SELECT count(*) FROM stac_higher.ingest_files WHERE status = 'extracting'"
    ),
```

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_extract.py tests/test_ingest_discover.py tests/test_ingest_recovery.py tests/test_ingest_itemize.py tests/test_contract_fixtures.py tests/test_loadgen.py -q && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/ingest services/pipeline/src/pipeline/loadgen/sample.py services/pipeline/tests/_ingest_fake.py services/pipeline/tests/test_ingest_extract.py services/pipeline/tests/test_ingest_discover.py services/pipeline/tests/test_ingest_recovery.py
git commit -m "feat(ingest): extracting status, reason/run/mtime ledger columns, extractor metadata strategy, extract-stall sweep (G-6, I-100)"
```

---

### Task 7: Pipeline process repo + trigger — kind, association-keyed coalescing, run reads

**Files:**
- Modify: `services/pipeline/src/pipeline/process/repo.py:31-46, 80-255, 265-317, 372-441, 445-529` (+ new methods)
- Modify: `services/pipeline/src/pipeline/process/trigger.py:37-108`
- Modify: `services/pipeline/src/pipeline/dispatcher/repo.py:182-208`
- Modify: `services/pipeline/tests/_process_fake.py`
- Test: `services/pipeline/tests/test_process_triggers.py`

**Interfaces:**
- Produces: `QueuedRun.association_id: str | None`; new `RunRecord(id, process_id, association_id, status, input_items)` dataclass; `ProcessRepo.process_kind(process_id) -> str | None`; `ProcessRepo.get_run(run_id) -> RunRecord | None`; `enqueue_run` / `enqueue_run_detailed` gain `association_id: str | None = None`; `trigger_run(..., association_id: str | None = None)`; `record_source_run` unchanged. Source-listing queries (`list_item_event_sources`, `list_due_cron_sources`, dispatcher `list_process_sources`) filter `p.kind = 'transform'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_process_triggers.py`:

```python
ASSOC = "55555555-5555-4555-8555-555555555555"


async def test_extractor_triggers_coalesce_per_association_until_claimed():
    repo = FakeProcessRepo()
    first = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None,
        association_id=ASSOC, input_items=[{"item_id": "a", "ledger_ids": ["1"]}], now=NOW,
    )
    second = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None,
        association_id=ASSOC, input_items=[{"item_id": "b", "ledger_ids": ["2"]}], now=NOW,
    )
    assert second.run_id == first.run_id and second.merged
    assert [i["item_id"] for i in repo.enqueued[0]["input_items"]] == ["a", "b"]
    assert repo.enqueued[0]["association_id"] == ASSOC

    claimed = await repo.claim_run(first.run_id, NOW)
    assert claimed is not None and claimed.association_id == ASSOC

    third = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None,
        association_id=ASSOC, input_items=[{"item_id": "c", "ledger_ids": ["3"]}], now=NOW,
    )
    assert third.run_id != first.run_id and not third.merged


async def test_a_source_less_association_less_trigger_never_coalesces():
    repo = FakeProcessRepo()
    a = await trigger_run(repo, process_id=PROC, revision_id=REV, source_id=None, input_items=[], now=NOW)
    b = await trigger_run(repo, process_id=PROC, revision_id=REV, source_id=None, input_items=[], now=NOW)
    assert a.run_id != b.run_id


async def test_get_run_returns_the_batch_for_finalize():
    repo = FakeProcessRepo()
    r = await trigger_run(
        repo, process_id=PROC, revision_id=REV, source_id=None, association_id=ASSOC,
        input_items=[{"item_id": "a", "ledger_ids": ["1"], "draft": {"id": "a"}}], now=NOW,
    )
    rec = await repo.get_run(r.run_id)
    assert rec is not None
    assert (rec.process_id, rec.association_id, rec.status) == (PROC, ASSOC, "queued")
    assert rec.input_items[0]["draft"] == {"id": "a"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py -q -k "extractor or association or get_run"`
Expected: FAIL — unexpected keyword `association_id`.

- [ ] **Step 3: Implement**

`process/repo.py`:
- `QueuedRun`: add `association_id: str | None = None` after `source_id` (keep it keyword-default so existing constructions compile).
- New dataclass after `ProcessCheckRequest`:

```python
@dataclass(frozen=True)
class RunRecord:
    """A run row as the finalize extract branch needs it (G-6)."""

    id: str
    process_id: str
    association_id: str | None
    status: str
    input_items: list[dict[str, Any]] = field(default_factory=list)
```

- ABC: new abstract methods

```python
    @abc.abstractmethod
    async def process_kind(self, process_id: str) -> str | None:
        """`transform` | `extractor`, or None when the process is gone
        (GOES spec §6). Read where the two kinds diverge — finalize — rather
        than carried on the run, so a process cannot change meaning mid-run
        (kind is create-only in the app anyway)."""

    @abc.abstractmethod
    async def get_run(self, run_id: str) -> RunRecord | None:
        """One run row's identity, status and input batch."""
```

  and `enqueue_run` / `enqueue_run_detailed` gain `association_id: str | None = None` (the ABC's default `enqueue_run_detailed` forwards it). Extend the `enqueue_run` docstring: "An extractor run names its ASSOCIATION instead of a source (§15) and coalesces per (process, association) the same way."
- Pg `enqueue_run_detailed`: after the `source_id` branch, add the twin:

```python
            if association_id is not None and not is_test:
                # §15: extractor runs coalesce per (process, association);
                # migration 027's process_runs_queued_association_idx is the
                # arbiter, the exact shape of the source branch above.
                cur = await conn.execute(
                    "INSERT INTO stac_higher.process_runs"
                    " (process_id, revision_id, source_id, association_id, input_items,"
                    "  rate_deferred_until, is_test)"
                    " VALUES (%s, %s, NULL, %s, %s::jsonb, %s, %s)"
                    " ON CONFLICT (process_id, association_id)"
                    "   WHERE status = 'queued' AND association_id IS NOT NULL"
                    " DO UPDATE SET input_items ="
                    "   stac_higher.process_runs.input_items || EXCLUDED.input_items"
                    " RETURNING id, (xmax = 0) AS inserted",
                    (process_id, revision_id, association_id, items, deferred_until, is_test),
                )
                row = await cur.fetchone()
                await conn.commit()
                if not row:
                    return None, False
                return str(row[0]), not bool(row[1])
```

  The plain INSERT at the end also writes `association_id` (add the column and value).
- `claim_due_runs` / `claim_run`: add `r.association_id` to `RETURNING` after `r.source_id` and shift the tuple indices (`association_id=str(r[4]) if r[4] else None`, attempts is now `r[5]`, and so on). Do this carefully in both statements — they must stay identical in shape.
- Pg `process_kind`:

```python
    async def process_kind(self, process_id: str) -> str | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT kind FROM stac_higher.processes WHERE id = %s AND deleted_at IS NULL",
                (process_id,),
            )
            row = await cur.fetchone()
        return str(row[0]) if row else None
```

- Pg `get_run`:

```python
    async def get_run(self, run_id: str) -> RunRecord | None:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, process_id, association_id, status, input_items"
                "  FROM stac_higher.process_runs WHERE id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
        if not row:
            return None
        return RunRecord(
            id=str(row[0]), process_id=str(row[1]),
            association_id=str(row[2]) if row[2] else None,
            status=str(row[3]), input_items=list(row[4] or []),
        )
```

- `list_item_event_sources` and `list_due_cron_sources`: add `"   AND p.kind = 'transform'"` to both WHERE clauses with the comment `# G-6: an extractor is never dispatched by item events or cron.` Same line in `dispatcher/repo.py` `list_process_sources`.

`process/trigger.py` `trigger_run`: new kwarg `association_id: str | None = None`, passed to `enqueue_run_detailed`, and logged in the deferral warning's `extra`. Docstring addition: "An extractor trigger (G-6) passes `association_id` and no source; coalescing keys on it."

`tests/_process_fake.py`: `enqueue_run`/`enqueue_run_detailed` accept `association_id=None`; the coalescing key becomes `("assoc", association_id)` when `association_id` is set (and `("src", source_id)` for sources) in `_queued`; the row records `"association_id"`; `claim_run` pops both keys and returns `association_id=row["association_id"]`; add `kinds: dict[str, str] = field(default_factory=dict)` with `process_kind` returning `self.kinds.get(process_id, "transform")`; `get_run` builds a `RunRecord` from the recorded row (`status` from the row).

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_triggers.py tests/test_dispatch_loop.py tests/test_process_finalize.py tests/test_process_reaper.py -q && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/process services/pipeline/src/pipeline/dispatcher/repo.py services/pipeline/tests/_process_fake.py services/pipeline/tests/test_process_triggers.py
git commit -m "feat(process): kind read, association-keyed run coalescing, get_run; extractors never dispatched as transforms (G-6)"
```

---

### Task 8: ITEMIZE splits at the seam — `complete_item` and the `extracting` branch

**Files:**
- Modify: `services/pipeline/src/pipeline/ingest/extract.py` (new `build_extractor_draft`; `build_item` returns it early)
- Modify: `services/pipeline/src/pipeline/ingest/itemize.py:95-219`
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py:155-193`
- Test: `services/pipeline/tests/test_ingest_itemize.py`, `services/pipeline/tests/test_ingest_jobs.py`

**Interfaces:**
- Produces: `build_extractor_draft(collection_id, item_id, members, cfg, asset_href_base) -> dict` (a defaults-only skeleton whose `properties.datetime` may be `None` and whose geometry is `None` unless the collection fallback was opted in); `ItemizeOutcome.status` gains `"extracting"` (with `detail = run_id`); `complete_item(repo, writer, adapter, *, association, config, item_id, members, item_dict) -> ItemizeOutcome` (validate → upsert → mark itemized → post-ingest → telemetry); `run_itemize(..., process_repo: ProcessRepo | None = None, enqueue_now=None, now=None)`; a new `ExtractTrigger` dataclass `{process_id, ledger_ids, draft}` is NOT needed — the `input_items` entry is a plain dict `{"item_id", "collection_id", "op": "insert", "ledger_ids": [...], "draft": {...}}`.
- Consumes: Task 6's ledger methods; Task 7's `trigger_run(association_id=…)`, `process_repo.process_kind`, `process_repo.current_revision`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ingest_itemize.py`:

```python
from _process_fake import FakeProcessRepo
from pipeline.ingest.repo import STATUS_EXTRACTING
from pipeline.ingest.itemize import complete_item

PROC = "5c9f1c2e-0000-4000-8000-0000000000e1"


def _extractor_assoc(repo_kind="extractor"):
    return _assoc(
        {
            "source_path": "/out",
            "metadata": {"strategy": "extractor", "extractor": {"process_id": PROC}},
        }
    )


async def test_extractor_strategy_parks_rows_and_triggers_the_run():
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    rid = await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    process_repo = FakeProcessRepo(kinds={PROC: "extractor"})
    enqueued: list[str] = []

    async def enqueue_now(run_id: str) -> None:
        enqueued.append(run_id)

    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=process_repo, enqueue_now=enqueue_now,
    )

    assert out.status == "extracting"
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_EXTRACTING
    assert row.extract_run_id == out.detail == enqueued[0]
    run = process_repo.enqueued[0]
    assert run["association_id"] == assoc.id and run["source_id"] is None
    (entry,) = run["input_items"]
    assert entry["item_id"] == "scene" and entry["collection_id"] == "col"
    assert entry["ledger_ids"] == [rid] and entry["op"] == "insert"
    assert entry["draft"]["id"] == "scene" and entry["draft"]["collection"] == "col"
    assert entry["draft"]["assets"]["scene.nc"]["href"] == "/api/assets/col/scene/scene.nc"
    # The draft is best-effort: no datetime default, so it is left for the extractor.
    assert entry["draft"]["properties"]["datetime"] is None
    assert entry["draft"]["geometry"] is None


async def test_extractor_strategy_fails_rows_when_the_process_is_unusable():
    repo = FakeIngestRepo()
    assoc = _extractor_assoc()
    await repo.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    # wrong kind
    out = await run_itemize(
        repo, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=FakeProcessRepo(kinds={PROC: "transform"}),
    )
    assert out.status == "failed" and "not an extractor" in out.detail
    row = await repo.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_FAILED and "not an extractor" in row.reason

    # nothing deployed
    repo2 = FakeIngestRepo()
    await repo2.insert_ledger_version(
        assoc.id, "scene.nc", version=1, status=STATUS_STORED, size=7, fingerprint="f"
    )
    out = await run_itemize(
        repo2, FakeWriter(), FakeAdapter(), FakeS3(),
        association=assoc, config=parse_ingest_config(assoc.config),
        item_id="scene", source_paths=["scene.nc"], bucket="b", asset_href_base="/api/assets",
        process_repo=FakeProcessRepo(kinds={PROC: "extractor"}, deployed_revision=None),
    )
    assert out.status == "failed" and "no deployed revision" in out.detail


async def test_complete_item_is_the_shared_tail():
    repo = FakeIngestRepo()
    assoc = _assoc({"source_path": "/out", "metadata": {"strategy": "defaults_only",
                    "defaults": {"datetime": "2021-01-01T00:00:00Z", "geometry": "collection"}}})
    rid = await repo.insert_ledger_version(
        assoc.id, "scene.bin", version=1, status=STATUS_EXTRACTING, size=3, fingerprint="f"
    )
    rows = await repo.get_ledger_entries([rid])
    writer = FakeWriter()
    item = _valid_item()
    item["geometry"] = {"type": "Point", "coordinates": [0, 0]}

    out = await complete_item(
        repo, writer, FakeAdapter(), association=assoc,
        config=parse_ingest_config(assoc.config), item_id="scene", members=rows, item_dict=item,
    )

    assert out.status == "itemized" and out.bytes == 3
    assert writer.items == [item]
    assert repo.rows[rid].status == STATUS_ITEMIZED and repo.rows[rid].item_id == "scene"
```

Add to `tests/test_ingest_jobs.py` a handler test in the style of `test_fetch_handler_enqueues_itemize_when_stored`: monkeypatch `run_itemize` to return `ItemizeOutcome("extracting", "scene", "run-1")` and assert the itemize handler bumps **no** flow stats (use a `FakeIngestRepo` as the loaded repo and assert `repo.flow_stats == {}`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_itemize.py tests/test_ingest_jobs.py -q`
Expected: FAIL — `run_itemize` has no `process_repo`; `complete_item` missing.

- [ ] **Step 3: Implement the draft builder**

`ingest/extract.py` — after `build_defaults_only`:

```python
def build_extractor_draft(
    collection_id: str,
    item_id: str,
    members: list[ExtractMember],
    cfg: MetadataConfig,
    asset_href_base: str,
) -> dict[str, Any]:
    """The DRAFT an extractor process starts from (GOES spec §6.1): the
    defaults-only skeleton with every asset attached, but tolerant of a
    missing datetime — the extractor's whole job is to supply what the
    platform cannot infer, so an unresolved default is `None` here rather
    than an error. Geometry is `None`; `build_item` may still layer the
    opt-in collection-extent fallback on top."""
    if not members:
        raise ExtractError("no members to itemize")
    primary = _primary(members)
    try:
        when: dt.datetime | None = resolve_datetime(None, cfg, primary)
    except ExtractError:
        when = None
    item = _base_item(
        collection_id, item_id, members, primary, when or dt.datetime.now(dt.UTC), asset_href_base
    )
    if when is None:
        item["properties"]["datetime"] = None
    return item
```

In `build_item`, before the `defaults_only` branch:

```python
    if cfg.strategy == "extractor":
        item = build_extractor_draft(collection_id, item_id, members, cfg, asset_href_base)
        if item.get("geometry") is None and collection_fallback is not None:
            # Only the cheap layer: no best-effort GDAL read (the extractor
            # will open the file itself), and no fail-fast — a null geometry
            # is the extractor's to fill.
            item["geometry"] = collection_fallback["geometry"]
            item["bbox"] = collection_fallback["bbox"]
            item["properties"]["stac_higher:geometry_source"] = collection_fallback["source"]
        return item
```

(Read `_resolve_geometry_fallback`'s collection-fallback branch first and copy the exact keys it sets from `collection_fallback` — `geometry`, `bbox`, and the provenance property — so the two paths stamp the same shape.)

- [ ] **Step 4: Implement the split**

`ingest/itemize.py`:
- `ItemizeOutcome.status` docstring: `"itemized" | "failed" | "skipped" | "extracting"` (detail = run id).
- New `complete_item` holding today's VALIDATE → UPSERT → mark → post-ingest → telemetry block verbatim (operating on `members: list[LedgerEntry]` instead of `stored`), returning the same outcomes. Failure markings call `_mark(repo, members, STATUS_FAILED, None, reason=...)` — extend `_mark` with `reason: str | None = None` forwarded to `set_ledger_status_many`. The reasons: `f"validation: {exc}"`, `f"collection missing: {exc}"`.
- `run_itemize` new kwargs: `process_repo: ProcessRepo | None = None`, `enqueue_now: Callable[[str], Awaitable[None]] | None = None`, `now: dt.datetime | None = None`. After `build_item` succeeds:

```python
    if cfg.strategy == "extractor":
        return await _hand_to_extractor(
            repo, process_repo, association=association, cfg=cfg, item_id=item_id,
            stored=stored, draft=item_dict, enqueue_now=enqueue_now,
            now=now or dt.datetime.now(dt.UTC),
        )
    return await complete_item(
        repo, writer, adapter, association=association, config=config,
        item_id=item_id, members=stored, item_dict=item_dict,
    )
```

with:

```python
async def _hand_to_extractor(
    repo: IngestRepo,
    process_repo: ProcessRepo | None,
    *,
    association: IngestAssociation,
    cfg: MetadataConfig,
    item_id: str,
    stored: list[LedgerEntry],
    draft: dict[str, Any],
    enqueue_now: Callable[[str], Awaitable[None]] | None,
    now: dt.datetime,
) -> ItemizeOutcome:
    """GOES spec §6.2 step 1: park the group as `extracting` and queue the
    extractor run with the draft in its batch. Everything that can be checked
    before spending a run — the process exists, is an extractor, is deployed —
    is checked here, and a miss fails the rows with the reason."""
    process_id = cfg.extractor_process_id or ""

    async def fail(reason: str) -> ItemizeOutcome:
        await _mark(repo, stored, STATUS_FAILED, None, reason=reason)
        logger.warning("itemize extractor unusable", extra={"item_id": item_id, "error": reason})
        return ItemizeOutcome("failed", item_id, reason)

    if process_repo is None:
        return await fail("extractor strategy but no process repository configured")
    kind = await process_repo.process_kind(process_id)
    if kind is None:
        return await fail(f"extractor process {process_id} does not exist")
    if kind != "extractor":
        return await fail(f"process {process_id} is a {kind}, not an extractor")
    revision_id = await process_repo.current_revision(process_id)
    if not revision_id:
        return await fail(f"extractor process {process_id} has no deployed revision")

    # Park FIRST (all-or-nothing), then trigger: a crash between the two
    # leaves rows `extracting` with no run id, which the extract-stall sweep
    # fails after its threshold. The reverse order could execute a run whose
    # rows a retry then rebuilds a second time.
    await _mark(repo, stored, STATUS_EXTRACTING, None)
    result = await trigger_run(
        process_repo,
        process_id=process_id,
        revision_id=revision_id,
        source_id=None,
        association_id=association.id,
        input_items=[
            {
                "item_id": item_id,
                "collection_id": association.collection_id,
                "op": "insert",
                "ledger_ids": [e.id for e in stored],
                "draft": draft,
            }
        ],
        now=now,
        enqueue_now=enqueue_now,
    )
    if result.run_id is None:
        return await fail("extractor run could not be queued")
    await repo.set_extract_run([e.id for e in stored], result.run_id)
    logger.info(
        "itemize handed to extractor",
        extra={"item_id": item_id, "run_id": result.run_id, "merged": result.merged},
    )
    return ItemizeOutcome("extracting", item_id, result.run_id)
```

Imports: `from pipeline.process.repo import ProcessRepo`, `from pipeline.process.trigger import trigger_run`, `from collections.abc import Awaitable, Callable`, `STATUS_EXTRACTING`. Check for an import cycle (`pipeline.process.trigger` imports `pipeline.metrics`, `process.rate`, `process.repo` — none import `ingest`); if ruff or pytest reports one, import `trigger_run` inside `_hand_to_extractor`.

Update the module docstring: one sentence on the extractor branch ("for `metadata.strategy: extractor` the group is parked `extracting` and handed to a run; finalize's extract branch calls `complete_item`").

`jobs/ingest.py` `itemize` handler: build `process_repo = PgProcessRepo(settings.database_url)` and pass `process_repo=process_repo, enqueue_now=_enqueue_run_now` into `run_itemize`, where at `register` scope:

```python
    async def _enqueue_run_now(run_id: str) -> None:
        # The same immediate-dispatch job the process trigger uses (G-3).
        await queue.enqueue(JOB_RUN_NOW, {"run_id": run_id})
```

(import `JOB_RUN_NOW` from `pipeline.jobs.process` and `PgProcessRepo` from `pipeline.process.repo`). The telemetry block: `"extracting"` writes nothing, like `"skipped"` — say so in the comment: "the extract finalize branch bumps the rollup when the item lands".

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_ingest_itemize.py tests/test_ingest_jobs.py tests/test_integration_itemize.py tests/test_ingest_extract.py -q && uv run ruff check .`
Expected: PASS (the integration test skips without `DATABASE_URL`).

```bash
git add services/pipeline/src/pipeline/ingest services/pipeline/src/pipeline/jobs/ingest.py services/pipeline/tests/test_ingest_itemize.py services/pipeline/tests/test_ingest_jobs.py
git commit -m "feat(ingest): ITEMIZE splits at the seam — complete_item tail, extractor draft handed to a run (G-6)"
```

---

### Task 9: The run side — draft-fed manifest, association-derived read grants, dead runs fail their batch

**Files:**
- Modify: `services/pipeline/src/pipeline/process/inputs.py:33-36, 104-197`
- Modify: `services/pipeline/src/pipeline/process/runner.py:54-224`
- Modify: `services/pipeline/src/pipeline/jobs/process.py:185-225`
- Create: `tests/contract-fixtures/process-extract-manifest.json`
- Modify: `tests/contract-fixtures/README.md` (one paragraph under `producer-golden`)
- Test: `services/pipeline/tests/test_process_inputs.py`, `services/pipeline/tests/test_process_triggers.py`

**Interfaces:**
- Produces: `KIND_EXTRACT = "extract"`; `plan_inputs` uses `ref["draft"]` as the document when present (no pgstac read needed); `run_one(..., on_dead: Callable[[QueuedRun, str], Awaitable[None]] | None = None)` — called from `_finish` when `status == "dead"` and `run.association_id` is set; `jobs/process.py` supplies `_fail_extract_batch` which marks every `ledger_ids` in the batch `failed` with reason `f"extractor run {run.id}: {error}"`.
- Consumes: `QueuedRun.association_id` (Task 7); `set_ledger_status_many(reason=…)` (Task 6).

- [ ] **Step 1: Write the failing tests**

`tests/contract-fixtures/process-extract-manifest.json`:

```json
{
  "style": "producer-golden",
  "$comment": "The manifest an EXTRACTOR run receives (GOES spec §6.1, §15): kind is `extract`, and each item is the DRAFT the platform built at ITEMIZE (carried in the run's input_items as `draft`, never read from pgstac — it is not catalogued yet). Assets follow the same bucket/key rules as a transform's: a canonical href reads in place under the association collection's read grant; an absolute href is staged. Only pytest consumes this file (services/pipeline/tests/test_process_inputs.py).",
  "version": 1,
  "given": {
    "run_id": "11111111-1111-4111-8111-111111111111",
    "process_id": "22222222-2222-4222-8222-222222222222",
    "batch_id": "b1",
    "bucket": "stac-higher",
    "asset_href_base": "/api/assets",
    "source_collections": ["goes-abi-mcmipc"],
    "refs": [
      {
        "item_id": "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172",
        "collection_id": "goes-abi-mcmipc",
        "op": "insert",
        "ledger_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"],
        "draft": {
          "type": "Feature", "stac_version": "1.0.0", "stac_extensions": [],
          "id": "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172", "collection": "goes-abi-mcmipc",
          "geometry": null, "bbox": null, "properties": { "datetime": null }, "links": [],
          "assets": { "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc": { "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/246/04/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc", "type": "application/x-netcdf", "roles": ["data"] } }
        }
      }
    ],
    "documents": {}
  },
  "expected": {
    "manifest_key": "staging/runs/11111111-1111-4111-8111-111111111111/inputs/b1/manifest.json",
    "read_prefixes": ["assets/goes-abi-mcmipc/"],
    "fetches": [
      {
        "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/246/04/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc",
        "key": "staging/runs/11111111-1111-4111-8111-111111111111/inputs/b1/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc",
        "item_id": "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172",
        "asset_key": "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc"
      }
    ],
    "manifest": {
      "version": 1,
      "run_id": "11111111-1111-4111-8111-111111111111",
      "process_id": "22222222-2222-4222-8222-222222222222",
      "batch_id": "b1",
      "kind": "extract",
      "items": [
        {
          "collection": "goes-abi-mcmipc",
          "op": "insert",
          "item": { "$ref": "given.refs.0.draft" },
          "assets": {
            "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc": {
              "bucket": "stac-higher",
              "key": "staging/runs/11111111-1111-4111-8111-111111111111/inputs/b1/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc",
              "staged": true,
              "href": "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/246/04/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc"
            }
          }
        }
      ]
    }
  }
}
```

Check the exact `fetches` entry shape against the existing `process-input-manifest.json` (`RemoteFetch` fields) and match it. In `tests/test_process_inputs.py` add:

```python
EXTRACT_FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests" / "contract-fixtures" / "process-extract-manifest.json").read_text()
)


def test_extract_plan_matches_the_golden_fixture():
    given, expected = EXTRACT_FIXTURE["given"], EXTRACT_FIXTURE["expected"]
    plan = plan_inputs(
        run_id=given["run_id"], process_id=given["process_id"], batch_id=given["batch_id"],
        kind="extract", refs=given["refs"], documents={},
        source_collections=given["source_collections"],
        bucket=given["bucket"], asset_href_base=given["asset_href_base"],
    )
    manifest = copy.deepcopy(expected["manifest"])
    manifest["items"][0]["item"] = given["refs"][0]["draft"]
    assert plan.manifest == manifest
    assert plan.manifest_key == expected["manifest_key"]
    assert list(plan.read_prefixes) == expected["read_prefixes"]
    assert [asdict(f) for f in plan.fetches] == expected["fetches"]


def test_a_draft_ref_needs_no_pgstac_document():
    plan = plan_inputs(
        run_id="r", process_id="p", batch_id="b", kind="extract",
        refs=[{"item_id": "i", "collection_id": "c", "draft": {"id": "i", "assets": {}}}],
        documents={}, source_collections=["c"], bucket="b", asset_href_base="/api/assets",
    )
    assert plan.manifest["items"][0]["item"] == {"id": "i", "assets": {}}
    assert "skipped" not in plan.manifest
```

`tests/test_process_triggers.py` — find the existing `run_one` test that drives a container to `dead` with `MemoryExecutor` (grep `"dead"` near `run_one`) and add a sibling:

```python
async def test_a_dead_extractor_run_reports_its_batch(...):
    # Same executor/settings setup as the neighbouring dead-run test, but the
    # QueuedRun carries association_id=ASSOC, attempts at the budget, and
    # input_items=[{"item_id": "a", "ledger_ids": ["1", "2"], "draft": {...}}].
    seen: list[tuple[str, str]] = []

    async def on_dead(run, error):
        seen.append((run.id, error))

    result = await run_one(run, ..., on_dead=on_dead)
    assert result.status == "dead"
    assert seen and seen[0][0] == run.id
```

and a twin asserting `on_dead` is NOT called for a `failed` (retry-pending) outcome.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_inputs.py tests/test_process_triggers.py -q -k "extract or draft or dead"`
Expected: FAIL — `kind` `extract` yields `skipped: not_found`; `run_one` has no `on_dead`.

- [ ] **Step 3: Implement the planner**

`process/inputs.py`: add `KIND_EXTRACT = "extract"` under `KIND_TRANSFORM`. In `plan_inputs`, replace the document lookup:

```python
        # G-6: an extractor ref CARRIES its document — the draft ITEMIZE built,
        # which is not in pgstac yet. A transform ref names a catalogued item.
        document = ref.get("draft") if isinstance(ref.get("draft"), dict) else None
        if document is None:
            document = documents.get((collection, item_id))
```

Docstring: add "A ref with a `draft` (extractor runs, GOES spec §6) is its own document."

- [ ] **Step 4: Implement the runner and the job**

`process/runner.py`:
- Import `KIND_EXTRACT`.
- `run_one` signature gains `on_dead: Callable[[QueuedRun, str], Awaitable[None]] | None = None` (import `Awaitable, Callable` from `collections.abc`).
- Replace the source-collection block:

```python
    # GOES spec §3: describe the inputs, stage the remote ones, THEN mint+launch.
    # §15: an extractor run (association-triggered) reads the collection its
    # association ingests into — its refs name it — since it has no sources.
    is_extract = run.association_id is not None
    if is_extract:
        source_collections = tuple(
            sorted({str(ref["collection_id"]) for ref in run.input_items if ref.get("collection_id")})
        )
    else:
        source_collections = await repo.list_source_collections(run.process_id)
    documents: dict[tuple[str, str], dict] = {}
    if not is_extract:
        for ref in run.input_items:
            ...  # existing loop unchanged
```

  and `kind=KIND_EXTRACT if is_extract else KIND_TRANSFORM` in `plan_inputs`.
- Every `_finish(...)` call passes `on_dead=on_dead` (add the kwarg to `_finish`), and `_finish` ends with:

```python
    # G-6: a dead extractor run fails every ledger row in its batch (spec
    # §6.2, §15: at TERMINAL dead, never on a retryable attempt — the retry
    # may still land the batch).
    if status == "dead" and run.association_id is not None and on_dead is not None:
        await on_dead(run, error or "run died")
```

`jobs/process.py` `_execute_claimed`: build once per batch

```python
        ingest_repo = PgIngestRepo(settings.database_url)

        async def _fail_extract_batch(run: QueuedRun, error: str) -> None:
            ids = [str(lid) for ref in run.input_items for lid in ref.get("ledger_ids", [])]
            if not ids:
                return
            await ingest_repo.set_ledger_status_many(
                ids, status=STATUS_FAILED, item_id=None,
                reason=f"extractor run {run.id}: {error}"[:500],
            )
            logger.warning(
                "extractor run died; its ingest batch is failed",
                extra={"run_id": run.id, "rows": len(ids)},
            )
```

  and pass `on_dead=_fail_extract_batch` to `run_one`. Imports: `PgIngestRepo`, `STATUS_FAILED` from `pipeline.ingest.repo`.

`tests/contract-fixtures/README.md` — under the `producer-golden` heading add: "`process-extract-manifest.json` is the extractor twin (G-6): `kind: extract`, `items[].item` is the ref's own `draft` (`$ref: given.refs.<n>.draft`), and `read_prefixes` come from the refs' collections rather than `process_sources`."

- [ ] **Step 5: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_inputs.py tests/test_process_triggers.py tests/test_process_staging.py tests/test_main_jobs.py -q && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/process/inputs.py services/pipeline/src/pipeline/process/runner.py services/pipeline/src/pipeline/jobs/process.py tests/contract-fixtures/process-extract-manifest.json tests/contract-fixtures/README.md services/pipeline/tests/test_process_inputs.py services/pipeline/tests/test_process_triggers.py
git commit -m "feat(process): extract manifests from the draft, association read grants, dead runs fail their ingest batch (G-6)"
```

---

### Task 9b: Reference-mode inputs resolve to their source hrefs (plan defect found in Task 9)

**Why this task exists.** The planner treats every canonical `/api/assets/{c}/{i}/{f}` href as platform-held. But `storage_mode: reference` items carry canonical hrefs in the catalog too — the APP resolves them to `ingest_files.source_href` at request time (`app/src/lib/storage/reference.ts::lookupReferenceHref`). So a reference-mode item feeding a transform (G-2) or an extractor (G-6) is planned as `assets/{c}/{i}/{f}`, a key that does not exist. This is exactly the GOES case (G-7). The fix mirrors the app's seam on the pipeline side: the runner asks the ledger for each item's reference source hrefs, and the planner stages those assets from the source instead of granting a non-existent key. Ruling recorded in the SDD ledger; spec §6.2's "reference-mode files must be staged for an extractor" holds, it just needed this resolution step.

**Files:**
- Modify: `services/pipeline/src/pipeline/process/repo.py` (ABC + Pg: `reference_source_hrefs`)
- Modify: `services/pipeline/src/pipeline/process/inputs.py` (`plan_inputs(..., source_hrefs=...)`)
- Modify: `services/pipeline/src/pipeline/process/runner.py` (query per item, pass through)
- Modify: `services/pipeline/tests/_process_fake.py` (`source_hrefs` field)
- Modify: `tests/contract-fixtures/process-extract-manifest.json` (the draft's asset href becomes CANONICAL; `given.source_hrefs` supplies the NODD URL; `expected` stages from it), `tests/contract-fixtures/README.md` (one sentence)
- Test: `services/pipeline/tests/test_process_inputs.py`, `services/pipeline/tests/test_process_triggers.py`

**Interfaces:**
- Produces: `ProcessRepo.reference_source_hrefs(collection_id: str, item_id: str) -> dict[str, str]` (filename → source href; empty for copy-mode / manual items); `plan_inputs(..., source_hrefs: Mapping[tuple[str, str], Mapping[str, str]] = {})`; the fake gains `source_hrefs: dict[tuple[str, str], dict[str, str]]`.
- Consumes: `parse_canonical_href`, `run_input_asset_key`, `RemoteFetch`, `InputAsset` (existing); `QueuedRun.association_id` (Task 7).

- [ ] **Step 1: Write the failing tests**

`tests/test_process_inputs.py` — add:

```python
def test_reference_mode_canonical_href_is_staged_from_its_source():
    doc = {
        "id": "i", "collection": "c",
        "assets": {"scene": {"href": "/api/assets/c/i/scene.nc"}},
    }
    plan = plan_inputs(
        run_id="r", process_id="p", batch_id="b", kind="transform",
        refs=[{"item_id": "i", "collection_id": "c"}],
        documents={("c", "i"): doc}, source_collections=["c"],
        bucket="stac-higher", asset_href_base="/api/assets",
        source_hrefs={("c", "i"): {"scene.nc": "https://src.example/x/scene.nc"}},
    )
    (fetch,) = plan.fetches
    assert fetch.href == "https://src.example/x/scene.nc"
    assert fetch.key == "staging/runs/r/inputs/b/i/scene.nc"
    asset = plan.manifest["items"][0]["assets"]["scene"]
    assert asset["staged"] is True and asset["key"] == fetch.key
    # The manifest keeps the CATALOG href for provenance, not the source URL.
    assert asset["href"] == "/api/assets/c/i/scene.nc"
    # Still granted: the collection prefix (a copy-mode sibling may need it).
    assert list(plan.read_prefixes) == ["assets/c/"]


def test_canonical_href_without_a_source_href_stays_platform_held():
    doc = {"id": "i", "collection": "c", "assets": {"a": {"href": "/api/assets/c/i/a.tif"}}}
    plan = plan_inputs(
        run_id="r", process_id="p", batch_id="b", kind="transform",
        refs=[{"item_id": "i", "collection_id": "c"}], documents={("c", "i"): doc},
        source_collections=["c"], bucket="stac-higher", asset_href_base="/api/assets",
        source_hrefs={("c", "i"): {"other.tif": "https://src.example/other.tif"}},
    )
    assert plan.fetches == ()
    assert plan.manifest["items"][0]["assets"]["a"]["key"] == "assets/c/i/a.tif"
```

Rewrite `tests/contract-fixtures/process-extract-manifest.json` so the draft's single asset href is the canonical `/api/assets/goes-abi-mcmipc/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc`, add

```json
"source_hrefs": {
  "goes-abi-mcmipc/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172": {
    "OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc":
      "https://noaa-goes19.s3.us-east-1.amazonaws.com/ABI-L2-MCMIPC/2026/246/04/OR_ABI-L2-MCMIPC-M6_G19_s20262460401172_e20262460403556_c20262460404061.nc"
  }
}
```

to `given`, and make `expected.fetches[0].href` the NODD URL while `expected.manifest.items[0].assets[<key>].href` is the canonical href (`staged: true`, key under `inputs/b1/<item_id>/<filename>`). Update `$comment` to say why (reference-mode items are catalogued with canonical hrefs; the pipeline resolves them through the ledger exactly as the app's asset route does). In `test_process_inputs.py`, the extract golden test passes `source_hrefs={tuple(k.split("/", 1)): v for k, v in given.get("source_hrefs", {}).items()}`.

`tests/test_process_triggers.py` — a `run_one` test (copy the neighbouring extract-run setup): `FakeProcessRepo(source_hrefs={("c", "a"): {"a.nc": "https://src.example/a.nc"}})`, a draft whose asset href is `/api/assets/c/a/a.nc`, a recording `fetch_remote` (`async def fetch_remote(href): seen.append(href); return b"bytes"`), and assert `seen == ["https://src.example/a.nc"]` after `run_one`.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_process_inputs.py tests/test_process_triggers.py -q -k "reference or platform_held or golden"`
Expected: FAIL — `plan_inputs` has no `source_hrefs`; the fixture asserts a fetch that is not produced.

- [ ] **Step 3: Implement**

`process/repo.py` — ABC:

```python
    @abc.abstractmethod
    async def reference_source_hrefs(self, collection_id: str, item_id: str) -> dict[str, str]:
        """filename -> source href for a `storage_mode: reference` item's
        assets, from the ingest ledger (empty for copy-mode or manual items).
        The catalog stores CANONICAL hrefs for reference items too; the app's
        asset route resolves them through this same ledger column
        (`storage/reference.ts`), and the run planner must do the same or it
        grants a key that does not exist."""
```

Pg (mirrors `lookupReferenceHref`, latest version per filename):

```python
    async def reference_source_hrefs(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, str]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (filename) filename, source_href FROM ("
                "  SELECT regexp_replace(f.source_path, '^.*/', '') AS filename,"
                "         f.source_href, f.version"
                "    FROM stac_higher.ingest_files f"
                "    JOIN stac_higher.collection_connections cc ON cc.id = f.association_id"
                "   WHERE cc.collection_id = %s AND f.item_id = %s"
                "     AND f.source_href IS NOT NULL"
                "     AND f.reference_removed_at IS NULL"
                ") x ORDER BY filename, version DESC",
                (collection_id, item_id),
            )
            rows = await cur.fetchall()
        return {str(r[0]): str(r[1]) for r in rows}
```

`process/inputs.py` — `plan_inputs` gains `source_hrefs: Mapping[tuple[str, str], Mapping[str, str]] | None = None`; inside the asset loop, the canonical branch becomes:

```python
            canonical = parse_canonical_href(href, asset_href_base)
            if canonical is not None:
                c, i, f = canonical
                source = (source_hrefs or {}).get((collection, item_id), {}).get(f)
                if source:
                    # A reference-mode asset: the catalog href is canonical but
                    # the bytes live at the source (ingest_files.source_href).
                    # Stage from there; keep the CATALOG href for provenance.
                    key = run_input_asset_key(run_id, batch_id, item_id, f)
                    fetches.append(RemoteFetch(href=source, key=key, item_id=item_id, asset_key=asset_key))
                    assets[asset_key] = asdict(InputAsset(bucket=bucket, key=key, staged=True, href=href))
                    continue
                assets[asset_key] = asdict(
                    InputAsset(bucket=bucket, key=canonical_asset_key(c, i, f), staged=False, href=href)
                )
                continue
```

Docstring: one sentence on `source_hrefs`. Module docstring: amend the first bullet ("a canonical href → the object already sits at … UNLESS the ledger says the item is reference-mode, in which case it is fetched from `source_href` like any remote asset").

`process/runner.py` — after `documents` are known (both branches), build the mapping only for items that have at least one canonical href:

```python
    # Reference-mode items are catalogued with canonical hrefs (the app resolves
    # them through ingest_files.source_href at request time); the planner needs
    # the same resolution or it grants a key that does not exist (G-6 Task 9b).
    source_hrefs: dict[tuple[str, str], dict[str, str]] = {}
    for (coll, item_id), doc in documents.items():
        if any(
            parse_canonical_href((a or {}).get("href"), settings.asset_href_base)
            for a in (doc.get("assets") or {}).values()
        ):
            source_hrefs[(coll, item_id)] = await repo.reference_source_hrefs(coll, item_id)
```

For extract runs `documents` is empty today (drafts ride in refs) — populate `documents[(coll, item_id)] = ref["draft"]` for extract refs before this loop so both kinds go through the same mapping, and pass `source_hrefs=source_hrefs` to `plan_inputs`. Import `parse_canonical_href` from `pipeline.process.inputs`.

`tests/_process_fake.py`: `source_hrefs: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)`; `async def reference_source_hrefs(self, collection_id, item_id): return dict(self.source_hrefs.get((collection_id, item_id), {}))`.

`tests/contract-fixtures/README.md` — under the extract-manifest paragraph add: "`given.source_hrefs` (optional, `{collection}/{item_id}` → `{filename: href}`) is what the ledger says about reference-mode assets; a canonical href with a matching entry is staged from that source and keeps its catalog href in the manifest."

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_process_inputs.py tests/test_process_triggers.py tests/test_process_staging.py -q && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/process services/pipeline/tests tests/contract-fixtures
git commit -m "fix(process): reference-mode inputs are staged from ingest_files.source_href, not granted a missing canonical key (G-6)"
```

---

### Task 10: Finalize's extract branch

**Files:**
- Create: `services/pipeline/src/pipeline/finalize/extract_run.py`
- Modify: `services/pipeline/src/pipeline/jobs/process.py:251-277` (`process_finalize`)
- Test: create `services/pipeline/tests/test_extract_finalize.py`

**Interfaces:**
- Produces: `check_extract_output(draft: dict, document: dict) -> str | None` (pure: the problem text or None); `finalize_extract_run(run_id, *, process_repo, ingest_repo, writer, store, adapter_for, now=None) -> ExtractFinalizeResult(itemized: int, failed: int, skipped: int)` where `adapter_for(association: IngestAssociation) -> StorageAdapter` builds the post-ingest adapter; `jobs/process.py::process_finalize` branches on `process_kind`.
- Consumes: `process_repo.get_run` / `process_kind` / `finish_run` (Task 7); `ingest_repo.get_association` / `get_ledger_entries` / `set_ledger_status_many(reason=…)` / `bump_flow_stats` (Task 6); `complete_item` (Task 8); `ObjectStore.list_keys/get/delete`.

- [ ] **Step 1: Write the failing tests**

`tests/test_extract_finalize.py`:

```python
"""Finalize's extract branch (GOES spec §6.2 step 3, §15)."""

from __future__ import annotations

import json

from _ingest_fake import FakeAdapter, FakeIngestRepo, FakeWriter, make_association
from _process_fake import FakeProcessRepo
from pipeline.finalize.extract_run import check_extract_output, finalize_extract_run
from pipeline.ingest.repo import STATUS_EXTRACTING, STATUS_FAILED, STATUS_ITEMIZED
from pipeline.storage.keys import run_staging_prefix

PROC = "5c9f1c2e-0000-4000-8000-0000000000e1"


class FakeStore:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.deleted: list[str] = []

    def list_keys(self, prefix):
        return [k for k in self.objects if k.startswith(prefix)]

    def get(self, key):
        return self.objects[key]

    def delete(self, key):
        self.deleted.append(key)

    def head(self, key):  # pragma: no cover
        return None

    def copy(self, s, d):  # pragma: no cover
        return None


def draft(item_id="scene", href="/api/assets/col/scene/scene.nc"):
    return {
        "type": "Feature", "stac_version": "1.0.0", "stac_extensions": [],
        "id": item_id, "collection": "col", "geometry": None, "bbox": None,
        "properties": {"datetime": None}, "links": [],
        "assets": {"scene.nc": {"href": href, "roles": ["data"]}},
    }


def fixed(d):
    out = json.loads(json.dumps(d))
    out["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    out["bbox"] = [0, 0, 0, 0]
    out["properties"] = {"datetime": "2026-09-03T04:01:17Z", "platform": "GOES-19"}
    out["assets"]["scene.nc"]["type"] = "application/x-netcdf"
    return out


# --- the pure check --------------------------------------------------------

def test_check_accepts_metadata_changes_only():
    assert check_extract_output(draft(), fixed(draft())) is None


def test_check_refuses_id_collection_href_and_asset_set_changes():
    d = draft()
    bad = fixed(d); bad["id"] = "other"
    assert "id" in check_extract_output(d, bad)
    bad = fixed(d); bad["collection"] = "elsewhere"
    assert "collection" in check_extract_output(d, bad)
    bad = fixed(d); bad["assets"]["scene.nc"]["href"] = "https://x/y.nc"
    assert "href" in check_extract_output(d, bad)
    bad = fixed(d); bad["assets"]["extra"] = {"href": "z"}
    assert "asset" in check_extract_output(d, bad)
    bad = fixed(d); del bad["assets"]["scene.nc"]
    assert "asset" in check_extract_output(d, bad)


def test_check_requires_geometry_and_datetime():
    d = draft()
    bad = fixed(d); bad["geometry"] = None
    assert "geometry" in check_extract_output(d, bad)
    bad = fixed(d); bad["properties"]["datetime"] = None
    assert "datetime" in check_extract_output(d, bad)


# --- the branch ------------------------------------------------------------

async def _setup(store_objects, *, rows=("scene",)):
    ingest = FakeIngestRepo()
    assoc = make_association(
        {"source_path": "/out", "metadata": {"strategy": "extractor", "extractor": {"process_id": PROC}}}
    )
    ingest.associations.append(assoc)
    refs = []
    for item_id in rows:
        rid = await ingest.insert_ledger_version(
            assoc.id, f"{item_id}.nc", version=1, status=STATUS_EXTRACTING, size=5, fingerprint="f"
        )
        refs.append({"item_id": item_id, "collection_id": "col", "op": "insert",
                     "ledger_ids": [rid], "draft": draft(item_id)})
    process = FakeProcessRepo(kinds={PROC: "extractor"})
    run_id, _ = await process.enqueue_run_detailed(
        process_id=PROC, revision_id="rev-1", source_id=None, association_id=assoc.id,
        input_items=refs, deferred_until=None,
    )
    await ingest.set_extract_run([r["ledger_ids"][0] for r in refs], run_id)
    return ingest, process, assoc, run_id


async def test_extract_branch_completes_the_item_and_bumps_flow_stats():
    ingest, process, assoc, run_id = await _setup({})
    prefix = run_staging_prefix(run_id)
    store = FakeStore({
        f"{prefix}scene.json": json.dumps(fixed(draft())).encode(),
        f"{prefix}inputs/b1/manifest.json": b"{}",
    })
    writer = FakeWriter()

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=writer, store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert (result.itemized, result.failed) == (1, 0)
    assert writer.items[0]["properties"]["platform"] == "GOES-19"
    row = await ingest.get_latest_ledger(assoc.id, "scene.nc")
    assert row.status == STATUS_ITEMIZED and row.item_id == "scene"
    assert ingest.flow_stats[assoc.id]["items"] == 1
    assert f"{prefix}scene.json" in store.deleted
    assert not process.finished  # a run that published is left `succeeded`


async def test_missing_or_bad_documents_fail_their_rows_with_a_reason():
    ingest, process, assoc, run_id = await _setup({}, rows=("a", "b", "c"))
    prefix = run_staging_prefix(run_id)
    tampered = fixed(draft("b")); tampered["id"] = "zzz"
    store = FakeStore({
        f"{prefix}a.json": b"not json",
        f"{prefix}b.json": json.dumps(tampered).encode(),
        # c.json absent
    })

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=FakeWriter(), store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert (result.itemized, result.failed) == (0, 3)
    for name in ("a", "b", "c"):
        row = await ingest.get_latest_ledger(assoc.id, f"{name}.nc")
        assert row.status == STATUS_FAILED and row.reason
    assert "no document" in (await ingest.get_latest_ledger(assoc.id, "c.nc")).reason
    assert ingest.flow_stats[assoc.id]["failed"] == 3
    # every output refused → the run is downgraded, mirroring the transform recorder
    assert process.finished[-1]["status"] == "dead"


async def test_extract_branch_is_idempotent_on_rows_that_moved_on():
    ingest, process, assoc, run_id = await _setup({})
    (rid,) = [r.id for r in ingest.rows.values()]
    await ingest.set_ledger_status_many([rid], status=STATUS_ITEMIZED, item_id="scene")
    prefix = run_staging_prefix(run_id)
    store = FakeStore({f"{prefix}scene.json": json.dumps(fixed(draft())).encode()})
    writer = FakeWriter()

    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=writer, store=store,
        adapter_for=lambda a: FakeAdapter(),
    )

    assert result.skipped == 1 and writer.items == []


async def test_extract_branch_fails_the_batch_when_the_association_is_gone():
    ingest, process, assoc, run_id = await _setup({})
    ingest.associations.clear()
    result = await finalize_extract_run(
        run_id, process_repo=process, ingest_repo=ingest, writer=FakeWriter(),
        store=FakeStore({}), adapter_for=lambda a: FakeAdapter(),
    )
    assert result.failed == 1
    row = next(iter(ingest.rows.values()))
    assert row.status == STATUS_FAILED and "association" in row.reason
```

`FakeProcessRepo(kinds=…)` and `enqueue_run_detailed(association_id=…)` are from Task 7.

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_extract_finalize.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

`services/pipeline/src/pipeline/finalize/extract_run.py`:

```python
"""Finalize's EXTRACT branch (GOES spec §6.2 step 3, §15).

An extractor run does not publish: it hands each fixed-up item back to the
ingest chain, which validates and writes it exactly as it would have without
the extractor — `complete_item` is the one implementation of that tail. So
this module is deliberately NOT a third ADR 0014 producer: the neutral steps
move assets and rewrite hrefs, and an extractor output has neither (its
assets are the ingest's own, already canonical or reference-style).

What it enforces (§6.1): the output document keeps the draft's id, collection
and asset set, and every asset href byte-for-byte; geometry and datetime
must be set (pgstac refuses a null geometry, and validation needs the
datetime). Anything else — properties, bbox, asset metadata — is the
extractor's to change.

Failure is per item (its ledger rows fail with the reason) and the run is
downgraded to `dead` only when NOTHING landed, mirroring the transform
recorder so "succeeded" cannot mean "published nothing".
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pipeline import metrics
from pipeline.connections.adapters.base import StorageAdapter
from pipeline.finalize.store import ObjectStore
from pipeline.ingest.config import parse_ingest_config
from pipeline.ingest.itemize import complete_item
from pipeline.ingest.repo import (
    STATUS_EXTRACTING,
    STATUS_FAILED,
    IngestAssociation,
    IngestRepo,
    LedgerEntry,
)
from pipeline.process.repo import ProcessRepo
from pipeline.stac.pgstac_writer import PgstacWriter
from pipeline.storage.keys import INPUTS_SEGMENT, run_staging_prefix

logger = logging.getLogger(__name__)

ITEM_DOCUMENT_SUFFIX = ".json"


@dataclass(frozen=True)
class ExtractFinalizeResult:
    itemized: int = 0
    failed: int = 0
    skipped: int = 0


def check_extract_output(draft: dict[str, Any], document: dict[str, Any]) -> str | None:
    """The §6.1 immutability rules, as the reason text or None when clean."""
    if document.get("id") != draft.get("id"):
        return f"extractor changed the item id ({draft.get('id')!r} -> {document.get('id')!r})"
    if document.get("collection") != draft.get("collection"):
        return "extractor changed the collection"
    draft_assets = draft.get("assets") or {}
    out_assets = document.get("assets") or {}
    if not isinstance(out_assets, dict) or set(out_assets) != set(draft_assets):
        return "extractor changed the asset set (assets may gain metadata, not members)"
    for key, entry in draft_assets.items():
        if (out_assets.get(key) or {}).get("href") != entry.get("href"):
            return f"extractor changed the href of asset {key!r}"
    if document.get("geometry") is None:
        return "extractor left geometry null"
    if not (document.get("properties") or {}).get("datetime"):
        return "extractor left properties.datetime unset"
    return None


async def finalize_extract_run(
    run_id: str,
    *,
    process_repo: ProcessRepo,
    ingest_repo: IngestRepo,
    writer: PgstacWriter,
    store: ObjectStore,
    adapter_for: Callable[[IngestAssociation], StorageAdapter],
    now: dt.datetime | None = None,
) -> ExtractFinalizeResult:
    run = await process_repo.get_run(run_id)
    if run is None or run.association_id is None:
        logger.warning("extract finalize: no such extractor run", extra={"run_id": run_id})
        return ExtractFinalizeResult()

    prefix = run_staging_prefix(run_id)
    keys = await asyncio.to_thread(store.list_keys, prefix)
    inputs_prefix = f"{prefix}{INPUTS_SEGMENT}/"
    outputs = {k for k in keys if not k.startswith(inputs_prefix) and k.endswith(ITEM_DOCUMENT_SUFFIX)}

    itemized = failed = skipped = 0

    async def fail_rows(rows: list[LedgerEntry], reason: str) -> None:
        nonlocal failed
        await ingest_repo.set_ledger_status_many(
            [r.id for r in rows], status=STATUS_FAILED, item_id=None, reason=reason[:500]
        )
        await ingest_repo.bump_flow_stats(run.association_id, failed=1)
        metrics.INGEST_EVENTS.labels(stage="failed").inc()
        failed += 1

    association = await ingest_repo.get_association(run.association_id)
    config = parse_ingest_config(association.config) if association else None
    adapter = adapter_for(association) if association else None

    for ref in run.input_items:
        item_id = str(ref.get("item_id") or "")
        ledger_ids = [str(x) for x in ref.get("ledger_ids", [])]
        rows = [
            r for r in await ingest_repo.get_ledger_entries(ledger_ids)
            if r.status == STATUS_EXTRACTING
        ]
        if not rows:
            # The idempotent guard ITEMIZE applies by path, applied by id: the
            # rows moved on (a retry landed them, or the sweep failed them).
            skipped += 1
            continue
        if association is None or config is None or adapter is None:
            await fail_rows(rows, "ingest association was disabled or deleted during extraction")
            continue
        key = f"{prefix}{item_id}{ITEM_DOCUMENT_SUFFIX}"
        if key not in outputs:
            await fail_rows(rows, f"extractor wrote no document for {item_id}")
            continue
        try:
            document = json.loads(await asyncio.to_thread(store.get, key))
        except Exception as err:
            await fail_rows(rows, f"extractor output for {item_id} is not readable JSON: {type(err).__name__}")
            continue
        if not isinstance(document, dict):
            await fail_rows(rows, f"extractor output for {item_id} is not a JSON object")
            continue
        problem = check_extract_output(ref.get("draft") or {}, document)
        if problem:
            await fail_rows(rows, problem)
            continue

        outcome = await complete_item(
            ingest_repo, writer, adapter,
            association=association, config=config, item_id=item_id,
            members=rows, item_dict=document,
        )
        if outcome.status == "itemized":
            await ingest_repo.bump_flow_stats(
                run.association_id, items=1, bytes_added=outcome.bytes,
                latency_seconds=outcome.latency_seconds,
            )
            metrics.INGEST_EVENTS.labels(stage="itemized_item").inc()
            metrics.INGEST_BYTES.inc(outcome.bytes or 0)
            itemized += 1
        else:
            # complete_item already marked the rows failed with its reason.
            await ingest_repo.bump_flow_stats(run.association_id, failed=1)
            metrics.INGEST_EVENTS.labels(stage="failed").inc()
            failed += 1

    # The run prefix is spent either way (inputs, manifest, documents); a
    # failed delete is not fatal — the staging TTL sweep ages it out.
    for key in keys:
        try:
            await asyncio.to_thread(store.delete, key)
        except Exception:  # pragma: no cover - best effort
            logger.warning("extract finalize: could not delete", extra={"key": key})

    if failed and not itemized:
        await process_repo.finish_run(
            run_id, status="dead",
            error=f"all {failed} extracted items were rejected",
            log_ref=None, next_attempt_at=None,
        )
    logger.info(
        "extractor run finalized",
        extra={"run_id": run_id, "itemized": itemized, "failed": failed, "skipped": skipped},
    )
    return ExtractFinalizeResult(itemized=itemized, failed=failed, skipped=skipped)
```

`FakeWriter.upsert_items` is async and `complete_item` awaits it; `FakeAdapter` satisfies `apply_post_ingest` for `leave`. If `bump_flow_stats` in the `FakeIngestRepo` records `failed` under a different key, match the fake's `apply_ingest_activity` output in the test rather than the code.

`jobs/process.py` `process_finalize` — before the outputs guard:

```python
        process_repo = _repo()
        # G-6: an extractor's outputs go back to INGEST, not to the catalog.
        # Branch before the output-collection guard — an extractor has none.
        if await process_repo.process_kind(process_id) == "extractor":
            master_key = load_key_or_skip(settings, JOB_FINALIZE)
            if master_key is None:
                return  # rows stay `extracting`; the stall sweep fails them
            ingest_repo = PgIngestRepo(settings.database_url)
            store = PlatformObjectStore(
                client=build_platform_client(settings), bucket=settings.staging_bucket
            )
            await finalize_extract_run(
                run_id,
                process_repo=process_repo,
                ingest_repo=ingest_repo,
                writer=PgPgstacWriter(settings.database_url),
                store=store,
                adapter_for=lambda assoc: build_adapter(
                    assoc.connection, master_key, settings.egress_allow_hosts
                ),
            )
            return
        outputs = await process_repo.list_output_collections(process_id)
        ...
```

(imports: `finalize_extract_run`, `build_adapter` from `pipeline.connections.build`.)

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_extract_finalize.py tests/test_process_finalize.py tests/test_finalize_seam.py tests/test_main_jobs.py -q && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/finalize/extract_run.py services/pipeline/src/pipeline/jobs/process.py services/pipeline/tests/test_extract_finalize.py
git commit -m "feat(finalize): extract branch — immutability checks, complete_item, per-item failure with reasons (G-6)"
```

---

### Task 11: Recovery sweep wiring and the loadgen `extractor` profile

**Files:**
- Modify: `services/pipeline/src/pipeline/jobs/ingest.py:196-222` (`recovery_sweep`)
- Modify: `services/pipeline/src/pipeline/loadgen/fixtures.py:31-54` (+ new constants), `services/pipeline/src/pipeline/loadgen/__main__.py:93-175, 328-366, 369-427`, `services/pipeline/src/pipeline/loadgen/report.py` (one row), `services/pipeline/src/pipeline/loadgen/README.md`
- Test: `services/pipeline/tests/test_loadgen.py`, `services/pipeline/tests/test_ingest_jobs.py`

**Interfaces:**
- Produces: `recovery_sweep` calls `sweep_stuck_extracting(settings.ingest_stored_stall_seconds)`; `METADATA_STRATEGIES` gains `"extractor"`; `metadata_config("extractor", process_id=…)`; `EXTRACTOR_PROCESS_ID` (fixed UUID `1d000000-0000-4000-8000-0000000000e1`), `EXTRACTOR_REVISION_ID`, `EXTRACTOR_CODE` (a pass-through extractor: copies each manifest item to `{item_id}.json`, setting `datetime` from the run's clock and a point geometry when null); `cmd_setup --metadata extractor` installs the process rows; `cmd_teardown` removes them.

- [ ] **Step 1: Write the failing tests**

`tests/test_loadgen.py` — append:

```python
def test_extractor_profile_round_trips_and_names_the_process():
    from pipeline.ingest.config import parse_ingest_config
    from pipeline.ingest.extract import parse_metadata
    from pipeline.loadgen.fixtures import EXTRACTOR_PROCESS_ID, ingest_config, metadata_config

    cfg = ingest_config("load/x/", metadata=metadata_config("extractor"))
    parsed = parse_ingest_config(cfg)
    meta = parse_metadata(parsed.metadata)
    assert meta.strategy == "extractor"
    assert meta.extractor_process_id == EXTRACTOR_PROCESS_ID


def test_extractor_code_is_a_pass_through():
    """The loadgen extractor must be executable and must keep id/collection/
    hrefs — the same rules finalize enforces — so the harness measures the
    extractor PATH, not a rejection loop."""
    from pipeline.loadgen.fixtures import EXTRACTOR_CODE

    compile(EXTRACTOR_CODE, "<extractor>", "exec")
    assert "STAC_HIGHER_INPUT_MANIFEST" in EXTRACTOR_CODE
    assert '["id"]' in EXTRACTOR_CODE and "datetime" in EXTRACTOR_CODE
```

`tests/test_ingest_jobs.py` — a `recovery_sweep` test in the file's monkeypatch style: replace `PgIngestRepo` with a `FakeIngestRepo` instance and assert `sweep_stuck_extracting` was awaited (give the fake a counter, e.g. set `repo.run_statuses` and an `extracting` row older than the threshold, then check it flipped to `failed`).

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_loadgen.py tests/test_ingest_jobs.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`jobs/ingest.py` `recovery_sweep`: after the stored sweep,

```python
        extract_failed = await repo.sweep_stuck_extracting(settings.ingest_stored_stall_seconds)
```

included in the `if` and logged as `"extracting_failed": extract_failed`. Comment: "G-6: `extracting` rows whose run vanished or closed without finalizing are failed; the failed-retry sweep re-drives the file."

`loadgen/fixtures.py`:

```python
METADATA_STRATEGIES = ("defaults_only", "raster_auto", "extractor")

#: The harness's pass-through extractor (G-6). Fixed ids so setup is
#: idempotent and teardown can find it, like pipeline.demo's process.
EXTRACTOR_PROCESS_ID = "1d000000-0000-4000-8000-0000000000e1"
EXTRACTOR_REVISION_ID = "1d000000-0000-4000-8000-0000000000e2"
EXTRACTOR_NAME = "m3-load-extractor"
EXTRACTOR_RUNTIME: dict[str, Any] = {
    "kind": "inline_python", "image": None, "memory_mb": 512, "timeout_seconds": 120,
    "retry": {"max_attempts": 2, "backoff": "exponential"},
    "network": {"level": "isolated", "hosts": []},
}
EXTRACTOR_CODE = '''\
"""Pass-through extractor: hand every draft back with a datetime and a
footprint, changing nothing the platform forbids changing."""
import datetime as dt
import json
import os

import boto3

s3 = boto3.client("s3")
bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
manifest = json.loads(
    s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
)
now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
for entry in manifest["items"]:
    item = entry["item"]
    if not item["properties"].get("datetime"):
        item["properties"]["datetime"] = now
    if item.get("geometry") is None:
        item["geometry"] = {"type": "Point", "coordinates": [0.0, 0.0]}
        item["bbox"] = [0.0, 0.0, 0.0, 0.0]
    item["properties"]["loadgen:extracted"] = True
    s3.put_object(Bucket=bucket, Key=f"{prefix}{item['id']}.json", Body=json.dumps(item).encode())
'''
```

and `metadata_config`:

```python
    if strategy == "extractor":
        return {"strategy": "extractor", "extractor": {"process_id": EXTRACTOR_PROCESS_ID}}
```

`loadgen/__main__.py`:
- `cmd_setup`: when `args.metadata == "extractor"`, after the source connection upsert call `_install_extractor(cur)`:

```python
def _install_extractor(cur) -> None:
    """The pass-through extractor the `extractor` profile names (G-6). Same
    direct-SQL split as the associations: the app owns the DDL, the harness
    writes rows."""
    cur.execute(
        "INSERT INTO stac_higher.processes"
        " (id, name, description, group_id, kind, enabled, max_runs_per_hour, created_by)"
        " VALUES (%s, %s, 'M3 load harness pass-through extractor', %s, 'extractor', true, 600, %s)"
        " ON CONFLICT (id) DO UPDATE SET enabled = true, deleted_at = NULL",
        (EXTRACTOR_PROCESS_ID, EXTRACTOR_NAME, LOAD_GROUP, LOAD_CREATED_BY),
    )
    cur.execute("UPDATE stac_higher.processes SET current_revision = NULL WHERE id = %s", (EXTRACTOR_PROCESS_ID,))
    cur.execute("DELETE FROM stac_higher.process_runs WHERE process_id = %s", (EXTRACTOR_PROCESS_ID,))
    cur.execute("DELETE FROM stac_higher.process_revisions WHERE process_id = %s", (EXTRACTOR_PROCESS_ID,))
    cur.execute(
        "INSERT INTO stac_higher.process_revisions (id, process_id, runtime, code, env, created_by)"
        " VALUES (%s, %s, %s::jsonb, %s, '[]'::jsonb, %s)",
        (EXTRACTOR_REVISION_ID, EXTRACTOR_PROCESS_ID, json.dumps(EXTRACTOR_RUNTIME), EXTRACTOR_CODE, LOAD_CREATED_BY),
    )
    cur.execute(
        "UPDATE stac_higher.processes SET current_revision = %s WHERE id = %s",
        (EXTRACTOR_REVISION_ID, EXTRACTOR_PROCESS_ID),
    )
```

- `cmd_teardown`: before deleting associations, delete `process_runs`, `process_revisions`, then `processes` rows for `EXTRACTOR_PROCESS_ID` (order: runs → revisions → process, after nulling `current_revision`).
- `build_parser`: `setup --metadata` help gains `extractor = the G-6 extractor path (pass-through extractor process, one run per batch)`; `feed --profile` help notes `extractor` pairs with `opaque`.
- `report.py`: add `ingest_files_extracting` to whatever row list renders the ledger counts (grep `ingest_files_stored`).
- `loadgen/README.md`: one paragraph "Extractor profile" — what it installs, that the process runtime image must exist (Docker executor), and that the number to read is `ingest_files_extracting` staying bounded while `itemized` climbs.

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest tests/test_loadgen.py tests/test_ingest_jobs.py -q && uv run ruff check .`
Expected: PASS.

```bash
git add services/pipeline/src/pipeline/jobs/ingest.py services/pipeline/src/pipeline/loadgen services/pipeline/tests/test_loadgen.py services/pipeline/tests/test_ingest_jobs.py
git commit -m "feat(pipeline): extract-stall recovery in the sweep; loadgen extractor profile (G-6)"
```

---

### Task 12: Docs, TODO, full gates

**Files:**
- Modify: `docs/processes.md` (new "Extractors" section after "How to publish an item"), `docs/FEATURES.md` (G-6 row), `docs/ISSUES.md` (I-100 → 🟢 with the commit; I-101 annotated with the cause), `docs/connections.md` or wherever the ingest `metadata` strategies are listed (`grep -rn "defaults_only" docs/*.md`), `TODO.md` (tick G-6; K-3 → migration 028; append the follow-ups), `docs/superpowers/specs/2026-09-01-goes-geocolor-loop-design.md` (Progress line)

- [ ] **Step 1: `docs/processes.md` — "Extractors"**

Insert after the "How to publish an item" section:

```markdown
## Extractors

A process created with **kind: extractor** is not wired to source and output
collections. It is selected on an **ingest association** (a collection's Data
flow tab → metadata strategy *extractor process*), and the platform hands it
every item that association brings in — **before** the item is catalogued —
so it can fix up what the built-in extraction could not infer: the datetime
from a filename, a footprint from a projection the platform does not read,
product-specific properties.

Your run receives the same manifest as a transform, with two differences:

- `kind` is `"extract"`.
- each `items[].item` is a **draft**: the id, collection and assets the
  platform built, a `datetime` that may be `null`, and a `geometry` that may
  be `null`. Asset `bucket`/`key` work as for a transform (reference-mode
  files are staged in for you).

Write **one `{item_id}.json` per input item** into your output prefix, and
nothing else — no asset files. The platform then validates and catalogues
each document as the ingest would have. These must not change, or the item
is refused with the reason on its ingest ledger row:

- `id` and `collection`;
- the set of asset keys, and every asset `href`.

Everything else is yours: `geometry`, `bbox`, `properties` (including
`datetime`, which **must** be set), and any per-asset metadata. An input with
no output document is refused too.

**Failure.** If the run fails, times out, or dies, every file in its batch
is marked `failed` on the ingest ledger with the run's error — there is no
fallback to the draft — and the ingest retries the file like any other
failure. Defaults for an extractor: 600 runs/hour and a 120 s timeout;
back-to-back files coalesce into one run while a run is still queued.

A minimal extractor:

```python
import json, os, boto3

s3 = boto3.client("s3")
bucket = os.environ["STAC_HIGHER_OUTPUT_BUCKET"]
prefix = os.environ["STAC_HIGHER_OUTPUT_PREFIX"]
manifest = json.loads(
    s3.get_object(Bucket=bucket, Key=os.environ["STAC_HIGHER_INPUT_MANIFEST"])["Body"].read()
)
for entry in manifest["items"]:
    item = entry["item"]
    item["properties"]["datetime"] = "2026-09-03T04:01:17Z"   # from the filename, say
    item["geometry"] = {"type": "Point", "coordinates": [-75.0, 0.0]}
    item["bbox"] = [-75.0, 0.0, -75.0, 0.0]
    s3.put_object(Bucket=bucket, Key=f"{prefix}{item['id']}.json", Body=json.dumps(item).encode())
```
```

Also change the "What your run receives" bullet about read access ("**read** the canonical prefixes of your process's source collections") to add: "— or, for an extractor, of the collection its association ingests into".

- [ ] **Step 2: FEATURES / ISSUES / spec / TODO**

`docs/FEATURES.md` GOES table: a `G-6 · Extractors | ✅ |` row summarising: kind column (027), `metadata.strategy: extractor`, `extracting` status + reason/run/mtime columns, association-keyed coalescing, `complete_item` tail, extract finalize branch, dead-run batch failure + stall sweep, display-only process → collection graph edge, UI (kind at create, badge, extractor card, picker), loadgen profile, I-100 fixed. Name the spec §15 addendum.

`docs/ISSUES.md`: I-100 → `🟢 resolved (G-6: DISCOVER persists source_mtime; file_mtime prefers it)` in place, then move the body to `ISSUES-ARCHIVE.md` per the file's own rule, leaving the stub line. I-101: append "**Cause (2026-09-02, G-6 planning):** the netCDF container dataset has no geotransform — only its subdatasets (`NETCDF:"file":CMI_C02`) are georeferenced, and `geometry_from_raster` opens the container. The GOES extractor (G-7) derives the footprint from the C02 subdataset; the built-in path is unchanged."

Spec `Progress:` line: add "G-6 merged <date>."

`TODO.md`: tick G-6 with a one-line note pointing at the plan; in K-3 change `Migration **027** (026 taken by W-2's retention cap, which landed first)` to `Migration **028** (026 taken by W-2, 027 by G-6's extractors)` — and the K spec's own text if it names 027 (`grep -n "027" docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md`). Append under "Discovered follow-ups" a `G-6 landed` entry listing the deviations actually taken (the plan's Task list is the record; note anything that differed).

- [ ] **Step 3: Full gates and commit**

Run: `npm run verify` (repo root) and `cd services/pipeline && uv run pytest -q && uv run ruff check .`
Expected: all green. Report the counts.

```bash
git add docs TODO.md
git commit -m "docs(extractors): processes.md section, FEATURES/ISSUES (I-100 closed, I-101 cause), tick G-6, K-3 -> migration 028"
```

Then merge per AGENTS.md: `git checkout ai/main && git merge ai/goes-g6 --no-ff`, re-run both gates on `ai/main`, remove the worktree.

---

### Task 13 (LEAD ONLY, Docker): live check on the compose stack

Not for the implementer. After the merge:

1. `docker compose build pipeline && docker compose up -d pipeline` (the run-e2e skill's stale-container gotcha); load any app page so migration 027 applies (`SELECT name FROM stac_higher.migrations ORDER BY name DESC LIMIT 1` → `027_extractors`).
2. In the UI: create a process with kind **extractor** (`/processes` → New process → Kind), deploy the pass-through code from `docs/processes.md`'s Extractors section, confirm the detail page shows the Extractor card and no Sources/Outputs, and that `POST /api/processes/{id}/sources` returns 409.
3. Point the seeded `goes-abi-mcmipc` association (or a fresh copy-mode association over the demo bucket) at it: Data flow → Edit → Metadata *extractor process* → pick it → Save. Watch the next poll: `SELECT status, reason, extract_run_id FROM stac_higher.ingest_files ORDER BY updated_at DESC LIMIT 5` should show `extracting` with a run id within a second of `stored`, then `itemized`; `/processes/{id}` shows the run; the item exists with `loadgen:extracted`-style properties from the pass-through.
4. Failure leg: deploy a revision whose code is `raise SystemExit(1)`, drop a file, and confirm the rows go `failed` with `reason` starting `extractor run …` once the run is `dead` (attempts exhausted), and that a fresh poll after the failed-retry threshold re-drives the file.
5. Loadgen (optional, quick): `uv run python -m pipeline.loadgen setup --metadata extractor --label g6 && … feed --profile opaque --count 50`; confirm `ingest_files_extracting` stays small and `itemized` reaches 50; `teardown`.
6. Record the findings in `TODO.md`'s follow-ups.

---

## Self-review (done while writing)

- **Spec coverage:** §6.1 kind + association selection + 409s + transform-cannot-be-named (Tasks 2, 3, 7); manifest `kind: extract` + draft (Tasks 8, 9); output rules (Task 10). §6.2 steps 1–4 (Tasks 8, 9, 10). §6.3 through the ledger (Task 8). §6.4 coalescing (Task 7, via §15's association key). §6.5 defaults (Tasks 2/5: 600 at create, 120 in the deploy form). §6.6 graph (Task 4, §15 endpoints). §10 tests: extract branch, `extracting` transitions, coalescing race, loadgen profile (Tasks 7–11). §15: every bullet has a task; I-100 in Task 6.
- **Placeholders:** none; the two UI tests describe interactions in prose where the exact Radix click sequence must be copied from the sibling test file, which is named.
- **Type consistency:** `complete_item(repo, writer, adapter, *, association, config, item_id, members, item_dict)` is used identically in Tasks 8 and 10; `trigger_run(..., association_id=…)` in Tasks 7 and 8; `QueuedRun.association_id` in Tasks 7, 9; `RunRecord` in Tasks 7, 10; `set_ledger_status_many(..., reason=…)` in Tasks 6, 8, 9, 10; `process_kind` in Tasks 7, 8, 10.
