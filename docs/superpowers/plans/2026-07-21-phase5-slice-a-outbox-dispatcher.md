# Phase 5 Slice A — Event Outbox + Dispatcher Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the durable event outbox (pgstac item changes → `item_events` + payload-less NOTIFY) and a poll-driven dispatcher skeleton that matches items to delivery associations (CQL2 `item_filter` + `asset_keys`) and logs the matched pairs — no byte transfer yet.

**Architecture:** A vendored statement-level trigger on `pgstac.items` inserts one row per item into `stac_higher.item_events` (app-owned migration 007, ADR 0007). A new pipeline `dispatcher/` co-process (run alongside the Procrastinate worker in `main.py`'s `asyncio.gather`) consumes pending outbox rows in `id` order, matches enabled `direction='deliver'` associations for the event's collection, applies the delivery `config` filters via a pure matcher, and logs `(item × association)` matches. The delivery `config` shape gets a Zod schema (app) + Python mirror (pipeline) as the cross-runtime contract.

**Tech Stack:** TypeScript/Astro + Zod (app control plane), Python 3.12 + psycopg + Procrastinate + cql2 (pipeline data plane), PostgreSQL/pgstac.

## Global Constraints

- **Base branch:** all work on a worktree off `ai/main` — `git worktree add .claude/worktrees/slice-a -b ai/slice-a ai/main`. Never commit to `main`.
- **Migration ownership (ADR 0001 + new ADR 0007):** the app owns ALL `stac_higher` DDL and, per ADR 0007, owns the trigger attached to `pgstac.items`. The pipeline runs NO DDL — it only reads/writes rows.
- **App-never-decrypts invariant:** the dispatcher skeleton opens NO connection sessions and decrypts NO credentials (it only reads DB rows + pgstac items). Byte transfer is Slice B.
- **Verify gate:** `npm run verify` (repo root) AND `cd services/pipeline && uv run pytest` must both pass before declaring done. Do NOT run e2e or Docker in a teammate context; live verification (Task 9) is lead-only.
- **Cross-runtime contract:** the delivery `config` field names/defaults MUST be identical between `app/src/lib/associations/schemas.ts` (Zod) and `services/pipeline/src/pipeline/delivery/config.py` (§5.1 of `ROADMAP.md`).
- **pgstac version is pinned** at `v0.9.11` (`docker-compose.yml`); the trigger path is validated against exactly that version.
- **Python style:** ruff clean (`uv run ruff check`), line-length 100, `from __future__ import annotations`, `# pragma: no cover` on psycopg methods (SQL is covered by live/integration runs, not unit tests) — matching `ingest/repo.py`.

---

## File Structure

**App (control plane):**
- Modify `app/src/lib/db/migrate.ts` — append migration 007 (`item_events` + trigger).
- Modify `app/src/lib/associations/schemas.ts` — add `deliveryConfigSchema`; convert the create payload to a direction-discriminated union; route `parseAssociationCreate` by direction.
- Modify `app/src/pages/api/collections/[id]/connections/index.ts` — skip the ingest-only `reference` check for delivery rows.
- Create `docs/decisions/0007-outbox-trigger-ownership.md` — the ADR (includes the A0 spike note).
- Test: `app/src/__tests__/associations-schemas.test.ts` (extend), and the delivery-create path in `app/src/__tests__/api-associations.test.ts`.

**Pipeline (data plane):**
- Create `services/pipeline/src/pipeline/delivery/__init__.py`
- Create `services/pipeline/src/pipeline/delivery/config.py` — `DeliveryConfig` + `parse_delivery_config` (Python mirror of the Zod schema).
- Create `services/pipeline/src/pipeline/delivery/matcher.py` — pure `match_item(item, associations)` → matches, applying `item_filter` (cql2) + `asset_keys`.
- Create `services/pipeline/src/pipeline/dispatcher/__init__.py`
- Create `services/pipeline/src/pipeline/dispatcher/repo.py` — `DispatchRepo` ABC + `PgDispatchRepo` (claim/mark outbox rows, read deliver associations, read pgstac item).
- Create `services/pipeline/src/pipeline/dispatcher/loop.py` — `dispatch_once(repo)` orchestration (poll one batch, match, log, mark processed).
- Modify `services/pipeline/src/pipeline/jobs/__init__.py` / add `services/pipeline/src/pipeline/jobs/dispatch.py` — register the poll-driven dispatcher tick.
- Modify `services/pipeline/src/pipeline/main.py` — wire `dispatch.register`.
- Modify `services/pipeline/pyproject.toml` — add `cql2` dependency.
- Tests: `test_delivery_config.py`, `test_delivery_matcher.py`, `test_dispatch_loop.py`, and a `_dispatch_fake.py` fake repo.

---

## Task 1: A0 — pgstac partitioned-items trigger spike (investigative gate)

This task is **exploratory, not TDD** — its output is a decision + a documented ADR note that the migration in Task 2 depends on. `pgstac.items` is partitioned by collection; statement-level triggers with transition tables (`REFERENCING NEW TABLE`) have documented limitations on partitioned parents. Determine the working mechanism before writing migration 007.

**Files:**
- Create: `docs/decisions/0007-outbox-trigger-ownership.md` (draft with the spike outcome)

- [ ] **Step 1: Bring up pgstac at the pinned version**

Run:
```bash
docker compose up -d pgstac
docker compose ps pgstac
```
Expected: `pgstac` healthy, image tag `v0.9.11`.

- [ ] **Step 2: Probe whether a statement-level trigger with transition tables fires on the partitioned parent**

Run (via `docker compose exec pgstac psql -U username -d postgis`, or `psql` on `localhost:5433`):
```sql
CREATE SCHEMA IF NOT EXISTS spike;
CREATE TABLE spike.events (id bigserial, item_id text, op text, at timestamptz DEFAULT now());

CREATE OR REPLACE FUNCTION spike.on_items() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (TG_OP = 'DELETE') THEN
    INSERT INTO spike.events (item_id, op) SELECT o.id, 'delete' FROM oldrows o;
  ELSE
    INSERT INTO spike.events (item_id, op) SELECT n.id, lower(TG_OP) FROM newrows n;
  END IF;
  RETURN NULL;
END $$;

-- Attempt the trigger on the partitioned PARENT with transition tables:
CREATE TRIGGER spike_items_ins AFTER INSERT ON pgstac.items
  REFERENCING NEW TABLE AS newrows FOR EACH STATEMENT EXECUTE FUNCTION spike.on_items();
```
Record the outcome: does `CREATE TRIGGER ... REFERENCING NEW TABLE` on the partitioned `pgstac.items` **succeed or raise** (e.g. `ERROR: ROW triggers with transition tables are not supported on partitioned tables` — or the statement-level equivalent)?

- [ ] **Step 3: Drive a real upsert and observe**

If the trigger was created, insert an item through pgstac's own path and check the events table:
```sql
SELECT count(*) FROM spike.events;   -- before
-- (upsert one item via pypgstac or a direct pgstac.create_item call)
SELECT count(*) FROM spike.events;   -- after — did exactly one row appear?
```
Also test whether the trigger must instead be attached **per-partition** (`pgstac.items` child partitions are named `_items_<n>` / by collection) — pgstac creates partitions dynamically per collection, which is the real risk: a parent trigger that does not cascade means new collections' partitions get no trigger.

- [ ] **Step 4: Decide the mechanism and write it into ADR 0007**

Pick ONE, based on what Steps 2–3 showed, and document the reasoning + the observed error/behavior verbatim in `docs/decisions/0007-outbox-trigger-ownership.md`:
- **(a) Statement-level trigger with transition tables on the parent** — only if it both created AND fired for dynamically-created partitions.
- **(b) Row-level `AFTER INSERT OR UPDATE OR DELETE` trigger on the parent** — row triggers DO cascade to partitions on partitioned tables; one `INSERT` into `item_events` per row. Simpler, cascades correctly; the tradeoff is per-row (not per-statement) firing, acceptable because it still writes to the durable outbox (not `pg_notify`).
- **(c) Poll pgstac's `pgstac_updated_at` / `items_deleted_log` change feed** — if triggers prove unworkable; note this defers to a polling consumer instead of a trigger.

Write the "Decision" and "Spike findings" sections. Leave "Status: proposed" until Task 2 lands the migration.

- [ ] **Step 5: Tear down the spike objects**

Run:
```sql
DROP TRIGGER IF EXISTS spike_items_ins ON pgstac.items;
DROP SCHEMA spike CASCADE;
```

- [ ] **Step 6: Commit the ADR draft**

```bash
git add docs/decisions/0007-outbox-trigger-ownership.md
git commit -m "docs(adr-0007): outbox trigger spike findings + chosen mechanism"
```

> **The mechanism chosen here (a/b/c) is referenced by Task 2 as `<TRIGGER-MECHANISM>`.** The default assumption for the code in Task 2 is **(b) row-level trigger** — the safest cascading choice — and Task 2's SQL is written for (b). If the spike selects (a) or (c), adjust Task 2's trigger SQL accordingly before implementing (the `item_events` table and the `pg_notify` wake are identical across all three).

---

## Task 2: Migration 007 — `item_events` outbox + trigger (app-owned)

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append to the `MIGRATIONS` array, after `006_ingest_files_source_href`)
- Finalize: `docs/decisions/0007-outbox-trigger-ownership.md`
- Verify against: live pgstac (this migration is verified by applying it, not by a unit test — matches how 003–006 are validated)

**Interfaces:**
- Produces: table `stac_higher.item_events (id BIGSERIAL PK, collection_id text, item_id text, op text, occurred_at timestamptz, processed_at timestamptz)`; a payload-less `NOTIFY item_events`; the pipeline `DispatchRepo` (Task 5) reads/updates this table.

- [ ] **Step 1: Append migration 007 to `migrate.ts`**

Add this object to the `MIGRATIONS` array (uses the **row-level trigger** mechanism — default per Task 1; swap the trigger body if the spike chose (a)/(c)):

```ts
  {
    // Phase 5 (ROADMAP §5.4, §6.4): the event outbox that bridges pgstac item
    // changes to the delivery dispatcher. ONE row per changed item into
    // stac_higher.item_events (durable — never a pg_notify payload, which caps
    // at ~8 KB and would abort bulk-upsert txns); a payload-less NOTIFY wakes
    // the dispatcher (Slice C). ADR 0007 licenses the app to attach this trigger
    // to pgstac.items (a table the app does not own) — the trigger writes ONLY
    // into stac_higher. Guarded on pgstac.items existing so a pgstac-less DB
    // (unit/CI) still migrates cleanly.
    //
    // Phase 6 hygiene (do NOT build now, mirrors audit_log/ingest_files): this
    // is an envelope-scale table — Phase 6 time-partitions it on occurred_at and
    // adds a partition-drop retention job.
    name: "007_item_events_outbox",
    sql: `
      CREATE TABLE IF NOT EXISTS stac_higher.item_events (
        id BIGSERIAL PRIMARY KEY,
        collection_id text NOT NULL,
        item_id text NOT NULL,
        op text NOT NULL CHECK (op IN ('insert','update','delete')),
        occurred_at timestamptz NOT NULL DEFAULT now(),
        processed_at timestamptz
      );

      -- The dispatcher claims pending rows in id order; this partial index keeps
      -- that scan cheap as processed rows accumulate (until Phase 6 partitions).
      CREATE INDEX IF NOT EXISTS item_events_pending_idx
        ON stac_higher.item_events (id)
        WHERE processed_at IS NULL;

      CREATE OR REPLACE FUNCTION stac_higher.item_events_capture()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (TG_OP = 'DELETE') THEN
          INSERT INTO stac_higher.item_events (collection_id, item_id, op)
            VALUES (OLD.collection, OLD.id, 'delete');
        ELSIF (TG_OP = 'UPDATE') THEN
          INSERT INTO stac_higher.item_events (collection_id, item_id, op)
            VALUES (NEW.collection, NEW.id, 'update');
        ELSE
          INSERT INTO stac_higher.item_events (collection_id, item_id, op)
            VALUES (NEW.collection, NEW.id, 'insert');
        END IF;
        -- Payload-less wake only — the payload is the outbox row, never NOTIFY.
        PERFORM pg_notify('item_events', '');
        RETURN NULL;
      END;
      $fn$;

      -- Attach only if pgstac.items exists (row-level cascades to partitions).
      DO $do$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM information_schema.tables
          WHERE table_schema = 'pgstac' AND table_name = 'items'
        ) THEN
          DROP TRIGGER IF EXISTS item_events_capture_trg ON pgstac.items;
          CREATE TRIGGER item_events_capture_trg
            AFTER INSERT OR UPDATE OR DELETE ON pgstac.items
            FOR EACH ROW EXECUTE FUNCTION stac_higher.item_events_capture();
        END IF;
      END;
      $do$;
    `,
  },
```

- [ ] **Step 2: Apply the migration against live pgstac**

The migration runs on the first API request (middleware). Trigger it:
```bash
docker compose up -d
cd app && npm run dev &   # or hit any /api route once
curl -s http://localhost:4321/api/collections/ >/dev/null
```
Then confirm the objects exist:
```bash
docker compose exec pgstac psql -U username -d postgis -c \
  "SELECT to_regclass('stac_higher.item_events'), tgname FROM pg_trigger WHERE tgname = 'item_events_capture_trg';"
```
Expected: `item_events` regclass non-null; one `item_events_capture_trg` row.

- [ ] **Step 3: Verify a real item change writes exactly one outbox row**

```bash
docker compose exec pgstac psql -U username -d postgis -c \
  "SELECT count(*) FROM stac_higher.item_events;"   # note the count
```
Upsert an item (via the app's item form, or an existing ingest e2e, or `pypgstac`), then re-run the count. Expected: exactly one new row per upserted item, `op='insert'` (or `'update'`), `processed_at IS NULL`.

- [ ] **Step 4: Finalize ADR 0007**

Set "Status: accepted". Confirm the "Decision" section states: app migration owns `item_events` + the pgstac-attached trigger; the trigger writes only into `stac_higher`; rejected alternative = pipeline DDL authority (splits ownership, violates ADR 0001's single-owner rule).

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/db/migrate.ts docs/decisions/0007-outbox-trigger-ownership.md
git commit -m "feat(db): migration 007 — item_events outbox + pgstac trigger (ADR 0007)"
```

---

## Task 3: Delivery `config` Zod schema (app, cross-runtime contract)

**Files:**
- Modify: `app/src/lib/associations/schemas.ts`
- Test: `app/src/__tests__/associations-schemas.test.ts`

**Interfaces:**
- Produces: `deliveryConfigSchema` (Zod), `DeliveryConfig` type; `associationCreateSchema` becomes a discriminated union on `direction`; `parseAssociationCreate` accepts `direction: 'deliver'`. Consumed by Task 4 (Python mirror — same field names/defaults) and Task 8 (route).

- [ ] **Step 1: Write failing tests for the delivery schema + discriminated create**

Add to `app/src/__tests__/associations-schemas.test.ts`:

```ts
import {
  deliveryConfigSchema,
  parseAssociationCreate,
} from "@/lib/associations/schemas";

describe("deliveryConfigSchema (§5.1)", () => {
  it("applies defaults for a minimal delivery config", () => {
    const parsed = deliveryConfigSchema.parse({
      path_template: "{collection}/{item_id}/{filename}",
    });
    expect(parsed.item_filter).toBeNull();
    expect(parsed.asset_keys).toBeNull();
    expect(parsed.payload).toEqual({
      item_json: false,
      checksums: null,
      completion_marker: false,
    });
    expect(parsed.on_update).toBe("redeliver");
    expect(parsed.overwrite).toBe("if_newer");
    expect(parsed.retry).toEqual({ max_attempts: 5, backoff: "exponential" });
    expect(parsed.max_concurrent_transfers).toBe(4);
  });

  it("requires a non-empty path_template", () => {
    expect(() => deliveryConfigSchema.parse({ path_template: "" })).toThrow();
  });

  it("accepts a delivery association create payload", () => {
    const result = parseAssociationCreate({
      connection_id: "11111111-1111-1111-1111-111111111111",
      direction: "deliver",
      config: { path_template: "{collection}/{item_id}/{filename}" },
    });
    expect(result.success).toBe(true);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd app && npx vitest run src/__tests__/associations-schemas.test.ts`
Expected: FAIL — `deliveryConfigSchema` is not exported; `parseAssociationCreate` rejects `direction: 'deliver'` with `DELIVERY_RESERVED_MESSAGE`.

- [ ] **Step 3: Add `deliveryConfigSchema` and rework the create union**

In `app/src/lib/associations/schemas.ts`, add after the ingest section:

```ts
// ---------------------------------------------------------------------------
// delivery config (stored as-is in collection_connections.config jsonb, §5.1)
// ---------------------------------------------------------------------------

const payloadSchema = z
  .object({
    item_json: z.boolean().default(false),
    // per-file checksum sidecars: null = none.
    checksums: z.enum(["md5", "sha256"]).nullable().default(null),
    // manifest written LAST — the "product complete" signal for watchers.
    completion_marker: z.boolean().default(false),
  })
  .strict();

const retrySchema = z
  .object({
    max_attempts: z.number().int().min(1).default(5),
    backoff: z.enum(["exponential", "fixed"]).default("exponential"),
  })
  .strict();

export const deliveryConfigSchema = z
  .object({
    // Rendered per asset — see delivery/path.py (Slice B). Tokens: {collection}
    // {item_id} {filename} {yyyy} {mm} {dd}.
    path_template: z.string().min(1, "path_template is required"),
    // optional CQL2 subset — null delivers every item.
    item_filter: z.string().min(1).nullable().default(null),
    // null = all assets; otherwise the asset keys to deliver.
    asset_keys: z.array(z.string().min(1)).nullable().default(null),
    payload: payloadSchema.default(() => payloadSchema.parse({})),
    on_update: z.enum(["redeliver", "ignore"]).default("redeliver"),
    overwrite: z.enum(["never", "always", "if_newer"]).default("if_newer"),
    retry: retrySchema.default(() => retrySchema.parse({})),
    max_concurrent_transfers: z.number().int().min(1).default(4),
  })
  .strict();

export type DeliveryConfig = z.infer<typeof deliveryConfigSchema>;
```

Then replace the ingest-only create schema with a direction-discriminated union. Change:

```ts
export const associationCreateSchema = z
  .object({
    connection_id: z.string().uuid("connection_id must be a connection UUID"),
    direction: z.literal("ingest"),
    enabled: z.boolean().default(true),
    config: ingestConfigSchema,
    expectation: expectationSchema.nullable().default(null),
  })
  .strict();
```

to:

```ts
const ingestCreateSchema = z
  .object({
    connection_id: z.string().uuid("connection_id must be a connection UUID"),
    direction: z.literal("ingest"),
    enabled: z.boolean().default(true),
    config: ingestConfigSchema,
    expectation: expectationSchema.nullable().default(null),
  })
  .strict();

const deliveryCreateSchema = z
  .object({
    connection_id: z.string().uuid("connection_id must be a connection UUID"),
    direction: z.literal("deliver"),
    enabled: z.boolean().default(true),
    config: deliveryConfigSchema,
    expectation: expectationSchema.nullable().default(null),
  })
  .strict();

export const associationCreateSchema = z.discriminatedUnion("direction", [
  ingestCreateSchema,
  deliveryCreateSchema,
]);
```

Update `AssociationCreateInput`:

```ts
export type AssociationCreateInput = z.infer<typeof associationCreateSchema>;
```

Then rewrite `parseAssociationCreate` to drop the `DELIVERY_RESERVED_MESSAGE` rejection and parse directly (the discriminated union now handles both directions; an unknown direction yields a clear union error):

```ts
export function parseAssociationCreate(data: unknown): ParsedCreate {
  return associationCreateSchema.safeParse(data);
}
```

Delete the now-unused `DELIVERY_RESERVED_MESSAGE` constant and the `customZodError` helper IF nothing else references them (grep first: `grep -rn "DELIVERY_RESERVED_MESSAGE\|customZodError" app/src`). If `associationUpdateSchema` still uses `ingestConfigSchema.optional()`, widen it to accept either config: `config: z.union([ingestConfigSchema, deliveryConfigSchema]).optional()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/associations-schemas.test.ts`
Expected: PASS (all, including the pre-existing ingest cases). If a pre-existing test asserted `DELIVERY_RESERVED_MESSAGE`, update it to assert the delivery-create now succeeds.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/associations/schemas.ts app/src/__tests__/associations-schemas.test.ts
git commit -m "feat(associations): delivery config Zod schema + direction-discriminated create (§5.1)"
```

---

## Task 4: Delivery `config` Python mirror (pipeline)

**Files:**
- Create: `services/pipeline/src/pipeline/delivery/__init__.py` (empty package marker)
- Create: `services/pipeline/src/pipeline/delivery/config.py`
- Test: `services/pipeline/tests/test_delivery_config.py`

**Interfaces:**
- Produces: `DeliveryConfig` (frozen dataclass), `parse_delivery_config(raw: dict) -> DeliveryConfig`, `DeliveryConfigError`. Field names/defaults MUST match Task 3's Zod schema. Consumed by Task 5 (matcher) and Slice B (workers).

- [ ] **Step 1: Write the failing test**

Create `services/pipeline/tests/test_delivery_config.py`:

```python
from pipeline.delivery.config import (
    DeliveryConfig,
    DeliveryConfigError,
    parse_delivery_config,
)


def test_minimal_config_applies_defaults():
    cfg = parse_delivery_config({"path_template": "{collection}/{item_id}/{filename}"})
    assert isinstance(cfg, DeliveryConfig)
    assert cfg.item_filter is None
    assert cfg.asset_keys is None
    assert cfg.payload == {"item_json": False, "checksums": None, "completion_marker": False}
    assert cfg.on_update == "redeliver"
    assert cfg.overwrite == "if_newer"
    assert cfg.max_concurrent_transfers == 4


def test_missing_path_template_raises():
    import pytest

    with pytest.raises(DeliveryConfigError):
        parse_delivery_config({})


def test_carries_item_filter_and_asset_keys():
    cfg = parse_delivery_config(
        {
            "path_template": "{item_id}/{filename}",
            "item_filter": "eo:cloud_cover < 10",
            "asset_keys": ["data", "thumbnail"],
        }
    )
    assert cfg.item_filter == "eo:cloud_cover < 10"
    assert cfg.asset_keys == ("data", "thumbnail")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_config.py -v`
Expected: FAIL — `pipeline.delivery` module does not exist.

- [ ] **Step 3: Write the implementation**

Create `services/pipeline/src/pipeline/delivery/__init__.py`:
```python
"""Delivery pipeline (ROADMAP §6.4): dispatch matching + workers (Slice B)."""
```

Create `services/pipeline/src/pipeline/delivery/config.py`:
```python
"""Typed view over a delivery association's ``config`` jsonb (ROADMAP §5.1).

Python side of the cross-runtime contract: the app writes the config through
``app/src/lib/associations/schemas.ts`` (Zod, which applies every default) and
the pipeline reads the same JSON back out of ``collection_connections.config``.
Field names and default values MUST NOT drift from ``deliveryConfigSchema``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

ON_UPDATE = ("redeliver", "ignore")
OVERWRITE = ("never", "always", "if_newer")
CHECKSUMS = ("md5", "sha256")
BACKOFF = ("exponential", "fixed")

DEFAULT_MAX_CONCURRENT_TRANSFERS = 4
DEFAULT_MAX_ATTEMPTS = 5


class DeliveryConfigError(ValueError):
    """The stored ``config`` jsonb is not a usable delivery config."""


@dataclass(frozen=True)
class DeliveryConfig:
    path_template: str
    item_filter: str | None = None
    asset_keys: tuple[str, ...] | None = None
    payload: dict[str, Any] = None  # type: ignore[assignment]  # set in parse
    on_update: str = "redeliver"
    overwrite: str = "if_newer"
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff: str = "exponential"
    max_concurrent_transfers: int = DEFAULT_MAX_CONCURRENT_TRANSFERS


def _enum(raw: Any, allowed: Sequence[str], default: str, field_name: str) -> str:
    if raw is None:
        return default
    value = str(raw)
    if value not in allowed:
        raise DeliveryConfigError(f"{field_name} must be one of {allowed}, got {value!r}")
    return value


def _opt_str_list(raw: Any) -> tuple[str, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise DeliveryConfigError("asset_keys must be an array of strings or null")
    return tuple(str(item) for item in raw)


def parse_delivery_config(raw: dict[str, Any]) -> DeliveryConfig:
    path_template = raw.get("path_template")
    if not isinstance(path_template, str) or not path_template.strip():
        raise DeliveryConfigError("path_template is required")

    payload_raw = raw.get("payload") or {}
    payload = {
        "item_json": bool(payload_raw.get("item_json", False)),
        "checksums": _enum_or_none(payload_raw.get("checksums"), CHECKSUMS, "payload.checksums"),
        "completion_marker": bool(payload_raw.get("completion_marker", False)),
    }

    retry_raw = raw.get("retry") or {}
    item_filter = raw.get("item_filter")
    return DeliveryConfig(
        path_template=path_template,
        item_filter=str(item_filter) if item_filter else None,
        asset_keys=_opt_str_list(raw.get("asset_keys")),
        payload=payload,
        on_update=_enum(raw.get("on_update"), ON_UPDATE, "redeliver", "on_update"),
        overwrite=_enum(raw.get("overwrite"), OVERWRITE, "if_newer", "overwrite"),
        max_attempts=int(retry_raw.get("max_attempts", DEFAULT_MAX_ATTEMPTS)),
        backoff=_enum(retry_raw.get("backoff"), BACKOFF, "exponential", "retry.backoff"),
        max_concurrent_transfers=int(
            raw.get("max_concurrent_transfers", DEFAULT_MAX_CONCURRENT_TRANSFERS)
        ),
    )


def _enum_or_none(raw: Any, allowed: Sequence[str], field_name: str) -> str | None:
    if raw is None:
        return None
    value = str(raw)
    if value not in allowed:
        raise DeliveryConfigError(f"{field_name} must be one of {allowed} or null, got {value!r}")
    return value
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_config.py -v && uv run ruff check src/pipeline/delivery`
Expected: PASS; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add services/pipeline/src/pipeline/delivery services/pipeline/tests/test_delivery_config.py
git commit -m "feat(pipeline): delivery config parser mirroring the Zod contract (§5.1)"
```

---

## Task 5: Delivery matcher (pure — CQL2 `item_filter` + `asset_keys`)

**Files:**
- Modify: `services/pipeline/pyproject.toml` (add `cql2` dependency)
- Create: `services/pipeline/src/pipeline/delivery/matcher.py`
- Test: `services/pipeline/tests/test_delivery_matcher.py`

**Interfaces:**
- Consumes: `DeliveryConfig` (Task 4).
- Produces:
  - `@dataclass(frozen=True) class DeliverAssociation: id: str; collection_id: str; config: dict[str, Any]`
  - `@dataclass(frozen=True) class Match: association_id: str; item_id: str; asset_keys: tuple[str, ...]`
  - `match_item(item: dict, associations: Sequence[DeliverAssociation]) -> list[Match]` — for each association whose `item_filter` passes (null = pass) and after intersecting `asset_keys` with the item's assets, returns a `Match` (skips an association whose filter fails or whose asset intersection is empty). Consumed by Task 6.

- [ ] **Step 1: Add the cql2 dependency**

In `services/pipeline/pyproject.toml`, add to `dependencies` (after `defusedxml`):
```toml
    "cql2>=0.3",
```
Run: `cd services/pipeline && uv sync`
Expected: `cql2` resolves and installs.

- [ ] **Step 2: Write the failing test**

Create `services/pipeline/tests/test_delivery_matcher.py`:
```python
from pipeline.delivery.matcher import DeliverAssociation, match_item

ITEM = {
    "id": "scene-1",
    "collection": "sensor-a",
    "properties": {"eo:cloud_cover": 5},
    "assets": {"data": {"href": "..."}, "thumbnail": {"href": "..."}},
}


def _assoc(aid, config):
    return DeliverAssociation(id=aid, collection_id="sensor-a", config=config)


def test_no_filter_matches_all_assets():
    matches = match_item(ITEM, [_assoc("a1", {"path_template": "{filename}"})])
    assert len(matches) == 1
    assert matches[0].association_id == "a1"
    assert set(matches[0].asset_keys) == {"data", "thumbnail"}


def test_item_filter_pass_and_fail():
    passing = _assoc("pass", {"path_template": "{filename}", "item_filter": "eo:cloud_cover < 10"})
    failing = _assoc("fail", {"path_template": "{filename}", "item_filter": "eo:cloud_cover > 50"})
    matches = match_item(ITEM, [passing, failing])
    assert [m.association_id for m in matches] == ["pass"]


def test_asset_keys_intersection():
    a = _assoc("a", {"path_template": "{filename}", "asset_keys": ["data", "missing"]})
    matches = match_item(ITEM, [a])
    assert set(matches[0].asset_keys) == {"data"}


def test_empty_asset_intersection_skips():
    a = _assoc("a", {"path_template": "{filename}", "asset_keys": ["missing"]})
    assert match_item(ITEM, [a]) == []
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_matcher.py -v`
Expected: FAIL — `pipeline.delivery.matcher` does not exist.

- [ ] **Step 4: Write the implementation**

Create `services/pipeline/src/pipeline/delivery/matcher.py`:
```python
"""Pure item→delivery-association matching (ROADMAP §6.4).

The dispatcher (Slice A skeleton) fetches the changed item + candidate
`direction='deliver'` associations, then calls :func:`match_item` to decide which
associations should receive the item and which of its assets. Kept pure (no DB,
no I/O) so it is fully unit-testable; the Pg wiring lives in dispatcher/repo.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from cql2 import Expr

from pipeline.delivery.config import parse_delivery_config


@dataclass(frozen=True)
class DeliverAssociation:
    """An enabled ``direction='deliver'`` association (config is raw §5.1 jsonb)."""

    id: str
    collection_id: str
    config: dict[str, Any]


@dataclass(frozen=True)
class Match:
    association_id: str
    item_id: str
    asset_keys: tuple[str, ...]


def _item_filter_passes(item_filter: str | None, item: dict[str, Any]) -> bool:
    """Evaluate a CQL2 text filter against a STAC item. Null filter = pass."""
    if not item_filter:
        return True
    return bool(Expr(item_filter).matches(item))


def match_item(
    item: dict[str, Any], associations: Sequence[DeliverAssociation]
) -> list[Match]:
    """Return one :class:`Match` per association that should receive ``item``.

    An association matches when its ``item_filter`` passes (null = all) AND the
    intersection of its ``asset_keys`` (null = all) with the item's assets is
    non-empty. The asset order follows the item's own asset declaration order.
    """
    item_id = str(item.get("id"))
    item_assets = list((item.get("assets") or {}).keys())
    matches: list[Match] = []
    for assoc in associations:
        cfg = parse_delivery_config(assoc.config)
        if not _item_filter_passes(cfg.item_filter, item):
            continue
        if cfg.asset_keys is None:
            keys = tuple(item_assets)
        else:
            wanted = set(cfg.asset_keys)
            keys = tuple(k for k in item_assets if k in wanted)
        if not keys:
            continue
        matches.append(Match(association_id=assoc.id, item_id=item_id, asset_keys=keys))
    return matches
```

> **cql2 API note:** the `cql2` package (cql2-rs bindings) exposes `Expr(text).matches(dict) -> bool`. If the installed version names it differently (e.g. `.matches()` vs a property-only dict), confirm with `uv run python -c "from cql2 import Expr; print(Expr('a < 1').matches({'properties':{'a':0}}))"` and adjust the one call site in `_item_filter_passes`. cql2 evaluates property references against the item's `properties` — pass the whole item dict.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd services/pipeline && uv run pytest tests/test_delivery_matcher.py -v && uv run ruff check src/pipeline/delivery`
Expected: PASS; ruff clean. (If `matches()` needs just `properties`, the note above tells you the fix.)

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/pyproject.toml services/pipeline/uv.lock services/pipeline/src/pipeline/delivery/matcher.py services/pipeline/tests/test_delivery_matcher.py
git commit -m "feat(pipeline): pure delivery matcher — cql2 item_filter + asset_keys"
```

---

## Task 6: Dispatcher repo + orchestration loop

**Files:**
- Create: `services/pipeline/src/pipeline/dispatcher/__init__.py`
- Create: `services/pipeline/src/pipeline/dispatcher/repo.py`
- Create: `services/pipeline/src/pipeline/dispatcher/loop.py`
- Create: `services/pipeline/tests/_dispatch_fake.py`
- Test: `services/pipeline/tests/test_dispatch_loop.py`

**Interfaces:**
- Consumes: `DeliverAssociation`, `Match`, `match_item` (Task 5).
- Produces:
  - `@dataclass(frozen=True) class ItemEvent: id: int; collection_id: str; item_id: str; op: str`
  - `class DispatchRepo(abc.ABC)` with: `claim_pending_events(limit: int) -> list[ItemEvent]`; `mark_processed(event_ids: Sequence[int]) -> None`; `list_deliver_associations(collection_id: str) -> list[DeliverAssociation]`; `get_item(collection_id: str, item_id: str) -> dict | None`.
  - `class PgDispatchRepo(DispatchRepo)` (psycopg, `# pragma: no cover`).
  - `async def dispatch_once(repo: DispatchRepo, *, batch_size: int = 100) -> list[Match]` — claim a batch, for each non-delete event fetch item + deliver-associations, collect matches (log each), mark ALL claimed events processed (including deletes and no-match, so the outbox drains), return the matches.

- [ ] **Step 1: Write the failing test + fake repo**

Create `services/pipeline/tests/_dispatch_fake.py`:
```python
"""In-memory DispatchRepo for dispatcher-loop unit tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.repo import DispatchRepo, ItemEvent


@dataclass
class FakeDispatchRepo(DispatchRepo):
    events: list[ItemEvent] = field(default_factory=list)
    associations: dict[str, list[DeliverAssociation]] = field(default_factory=dict)
    items: dict[tuple[str, str], dict] = field(default_factory=dict)
    processed: list[int] = field(default_factory=list)

    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:
        pending = [e for e in self.events if e.id not in self.processed]
        return pending[:limit]

    async def mark_processed(self, event_ids: Sequence[int]) -> None:
        self.processed.extend(event_ids)

    async def list_deliver_associations(self, collection_id: str) -> list[DeliverAssociation]:
        return self.associations.get(collection_id, [])

    async def get_item(self, collection_id: str, item_id: str) -> dict | None:
        return self.items.get((collection_id, item_id))
```

Create `services/pipeline/tests/test_dispatch_loop.py`:
```python
import pytest

from pipeline.delivery.matcher import DeliverAssociation
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import ItemEvent
from tests._dispatch_fake import FakeDispatchRepo

pytestmark = pytest.mark.asyncio


def _item(item_id):
    return {"id": item_id, "collection": "c", "properties": {}, "assets": {"data": {}}}


async def test_matches_and_drains_outbox():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=1, collection_id="c", item_id="i1", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={("c", "i1"): _item("i1")},
    )
    matches = await dispatch_once(repo)
    assert [m.association_id for m in matches] == ["a1"]
    assert repo.processed == [1]


async def test_delete_event_is_drained_without_matching():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=2, collection_id="c", item_id="gone", op="delete")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
    )
    matches = await dispatch_once(repo)
    assert matches == []
    assert repo.processed == [2]  # deletions never propagate, but the row drains


async def test_missing_item_drains_without_crashing():
    repo = FakeDispatchRepo(
        events=[ItemEvent(id=3, collection_id="c", item_id="race", op="insert")],
        associations={"c": [DeliverAssociation("a1", "c", {"path_template": "{filename}"})]},
        items={},  # item not yet visible (race) — skip, drain, revisit never (best-effort)
    )
    matches = await dispatch_once(repo)
    assert matches == []
    assert repo.processed == [3]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/pipeline && uv run pytest tests/test_dispatch_loop.py -v`
Expected: FAIL — `pipeline.dispatcher` does not exist.

- [ ] **Step 3: Write the repo**

Create `services/pipeline/src/pipeline/dispatcher/__init__.py`:
```python
"""Delivery dispatcher (ROADMAP §5.4, §6.4): outbox consumer + association match."""
```

Create `services/pipeline/src/pipeline/dispatcher/repo.py`:
```python
"""Repository seam over the event outbox + delivery associations + pgstac items.

Mirrors pipeline.ingest.repo: a DispatchRepo ABC the loop depends on (unit-tested
against an in-memory fake) plus a psycopg PgDispatchRepo for production. Pg
methods open a short-lived AsyncConnection and are ``# pragma: no cover`` — the
SQL is exercised by the live dispatch verification (Task 9), not unit tests.

Ownership (ADR 0001/0007): reads stac_higher.item_events + collection_connections
and pgstac items; UPDATEs only item_events.processed_at. Never runs DDL.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pipeline.delivery.matcher import DeliverAssociation


@dataclass(frozen=True)
class ItemEvent:
    id: int
    collection_id: str
    item_id: str
    op: str


class DispatchRepo(abc.ABC):
    @abc.abstractmethod
    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:
        """Pending outbox rows in id order (FOR UPDATE SKIP LOCKED in Pg)."""

    @abc.abstractmethod
    async def mark_processed(self, event_ids: Sequence[int]) -> None:
        """Stamp processed_at = now() for the given event ids."""

    @abc.abstractmethod
    async def list_deliver_associations(self, collection_id: str) -> list[DeliverAssociation]:
        """Enabled direction='deliver' associations for a collection."""

    @abc.abstractmethod
    async def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """The full STAC item from pgstac, or None if not (yet) present."""


@dataclass
class PgDispatchRepo(DispatchRepo):
    database_url: str

    async def _connect(self):  # pragma: no cover - thin psycopg wrapper
        import psycopg

        return await psycopg.AsyncConnection.connect(self.database_url)

    async def claim_pending_events(self, limit: int) -> list[ItemEvent]:  # pragma: no cover
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT id, collection_id, item_id, op FROM stac_higher.item_events"
                " WHERE processed_at IS NULL ORDER BY id"
                " FOR UPDATE SKIP LOCKED LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [ItemEvent(id=int(r[0]), collection_id=r[1], item_id=r[2], op=r[3]) for r in rows]

    async def mark_processed(self, event_ids: Sequence[int]) -> None:  # pragma: no cover
        if not event_ids:
            return
        async with await self._connect() as conn:
            await conn.execute(
                "UPDATE stac_higher.item_events SET processed_at = now()"
                " WHERE id = ANY(%s)",
                (list(event_ids),),
            )
            await conn.commit()

    async def list_deliver_associations(  # pragma: no cover
        self, collection_id: str
    ) -> list[DeliverAssociation]:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT cc.id, cc.collection_id, cc.config"
                " FROM stac_higher.collection_connections cc"
                " JOIN stac_higher.connections c ON c.id = cc.connection_id"
                " WHERE cc.collection_id = %s AND cc.direction = 'deliver'"
                " AND cc.enabled = true AND c.enabled = true",
                (collection_id,),
            )
            rows = await cur.fetchall()
        return [
            DeliverAssociation(id=str(r[0]), collection_id=r[1], config=dict(r[2]) if r[2] else {})
            for r in rows
        ]

    async def get_item(  # pragma: no cover
        self, collection_id: str, item_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            cur = await conn.execute(
                "SELECT pgstac.get_item(%s, %s)", (item_id, collection_id)
            )
            row = await cur.fetchone()
        return dict(row[0]) if row and row[0] else None
```

- [ ] **Step 4: Write the loop**

Create `services/pipeline/src/pipeline/dispatcher/loop.py`:
```python
"""Poll-driven dispatch orchestration (Slice A skeleton — logs, no transfer).

dispatch_once claims one batch of pending outbox rows, matches each non-delete
item against its collection's delivery associations, LOGS the matched pairs, and
marks the whole claimed batch processed so the outbox drains. Slice B replaces
the log with delivery-job fan-out; Slice C swaps the poll for a LISTEN wake.

Finalize-gating seam (ROADMAP §6.4, deferred to Phase 7): once externally-
writable collections exist, insert events for items still in staging must be
deferred until finalize marks them ready. No such collections exist yet, so the
skeleton dispatches every insert; this comment marks where that gate lands.
"""

from __future__ import annotations

import logging

from pipeline.delivery.matcher import Match, match_item
from pipeline.dispatcher.repo import DispatchRepo

logger = logging.getLogger(__name__)


async def dispatch_once(repo: DispatchRepo, *, batch_size: int = 100) -> list[Match]:
    events = await repo.claim_pending_events(batch_size)
    if not events:
        return []

    matches: list[Match] = []
    for event in events:
        # Deletions never propagate to destinations (ROADMAP §6.4) — drain only.
        if event.op == "delete":
            continue
        item = await repo.get_item(event.collection_id, event.item_id)
        if item is None:
            # Race: the outbox row beat the item's visibility. Best-effort skip;
            # a subsequent update event (or Slice C's revisit) re-drives it.
            logger.warning(
                "dispatch: item not found for event",
                extra={"collection_id": event.collection_id, "item_id": event.item_id},
            )
            continue
        associations = await repo.list_deliver_associations(event.collection_id)
        item_matches = match_item(item, associations)
        for m in item_matches:
            logger.info(
                "dispatch match (skeleton — no transfer yet)",
                extra={
                    "association_id": m.association_id,
                    "item_id": m.item_id,
                    "asset_keys": list(m.asset_keys),
                    "op": event.op,
                },
            )
        matches.extend(item_matches)

    await repo.mark_processed([e.id for e in events])
    return matches
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_dispatch_loop.py -v && uv run ruff check src/pipeline/dispatcher`
Expected: PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/dispatcher services/pipeline/tests/_dispatch_fake.py services/pipeline/tests/test_dispatch_loop.py
git commit -m "feat(pipeline): dispatcher outbox consumer + poll-driven dispatch_once skeleton"
```

---

## Task 7: Register the poll-driven dispatch tick

**Files:**
- Create: `services/pipeline/src/pipeline/jobs/dispatch.py`
- Modify: `services/pipeline/src/pipeline/main.py`
- Test: `services/pipeline/tests/test_main_jobs.py` (extend)

**Interfaces:**
- Consumes: `dispatch_once`, `PgDispatchRepo` (Task 6); `QueueBackend`, `Settings`.
- Produces: `register(queue, settings)` registering a periodic `pipeline.dispatch_poll` task (`* * * * *`) that runs `dispatch_once` against a fresh `PgDispatchRepo`. (Poll-driven now; Slice C adds the LISTEN co-process.)

- [ ] **Step 1: Write the failing test**

Add to `services/pipeline/tests/test_main_jobs.py` (follow the file's existing pattern for asserting a job registered on a fake/memory queue — mirror how `ingest.register` is asserted). Add:
```python
def test_dispatch_registers_poll(memory_queue_and_settings):
    queue, settings = memory_queue_and_settings
    from pipeline.jobs import dispatch

    dispatch.register(queue, settings)
    assert "pipeline.dispatch_poll" in queue.registered_periodic_names()
```
> Match the existing helper/fixture names in `test_main_jobs.py`. If that file asserts registration differently (e.g. inspecting `queue._periodic`), copy that exact assertion style instead of `registered_periodic_names()`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd services/pipeline && uv run pytest tests/test_main_jobs.py -k dispatch -v`
Expected: FAIL — `pipeline.jobs.dispatch` does not exist.

- [ ] **Step 3: Write the job registration**

Create `services/pipeline/src/pipeline/jobs/dispatch.py`:
```python
"""Delivery dispatch wiring (Slice A: poll-driven skeleton).

A periodic ``dispatch_poll`` task drains the item_events outbox each minute via
``dispatch_once`` (matches → log, no transfer yet). Slice C replaces this poll
with a LISTEN-woken co-process for single-digit-second latency; the periodic tick
stays as the safety-net fallback.
"""

from __future__ import annotations

import logging

from pipeline.config import Settings
from pipeline.dispatcher.loop import dispatch_once
from pipeline.dispatcher.repo import PgDispatchRepo
from pipeline.queue.interface import QueueBackend

logger = logging.getLogger(__name__)

JOB_DISPATCH_POLL = "pipeline.dispatch_poll"
CRON = "* * * * *"


def register(queue: QueueBackend, settings: Settings) -> None:
    async def dispatch_poll(timestamp: int) -> None:
        repo = PgDispatchRepo(settings.database_url)
        matches = await dispatch_once(repo)
        if matches:
            logger.info(
                "dispatch poll produced matches",
                extra={"matches": len(matches), "scheduled_timestamp": timestamp},
            )

    queue.register_periodic(dispatch_poll, name=JOB_DISPATCH_POLL, cron=CRON)
```

- [ ] **Step 4: Wire it into `main.py`**

In `services/pipeline/src/pipeline/main.py`, add the import and registration in `build_queue`:
```python
from pipeline.jobs import drain, health_sweep, heartbeat, ingest, staging_cleanup
```
becomes:
```python
from pipeline.jobs import (
    dispatch,
    drain,
    health_sweep,
    heartbeat,
    ingest,
    staging_cleanup,
)
```
and after `ingest.register(queue, settings)` add:
```python
    # Phase 5 Slice A: poll-driven delivery dispatch (outbox → match → log).
    dispatch.register(queue, settings)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd services/pipeline && uv run pytest tests/test_main_jobs.py -v && uv run ruff check src/pipeline`
Expected: PASS; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add services/pipeline/src/pipeline/jobs/dispatch.py services/pipeline/src/pipeline/main.py services/pipeline/tests/test_main_jobs.py
git commit -m "feat(pipeline): register poll-driven dispatch_poll tick"
```

---

## Task 8: Delivery-create route path (skip ingest-only reference check)

**Files:**
- Modify: `app/src/pages/api/collections/[id]/connections/index.ts`
- Test: `app/src/__tests__/api-associations.test.ts` (extend)

**Interfaces:**
- Consumes: `parseAssociationCreate` (now direction-discriminated, Task 3); `createAssociation` (already direction-agnostic).
- Produces: a delivery association is creatable via `POST` (operator+, group-owned); the `storage_mode: reference` check only applies to ingest.

- [ ] **Step 1: Write the failing test**

Add to `app/src/__tests__/api-associations.test.ts` a case that POSTs a `direction: 'deliver'` payload and asserts a 201 (follow the file's existing mocking pattern for `resolveUsableConnection` / `canManageCollection` / `createAssociation`). Example shape:
```ts
it("creates a delivery association (operator)", async () => {
  // arrange mocks exactly as the existing ingest-create test does, but with an
  // s3 (or any) connection and a delivery config:
  const body = {
    connection_id: "11111111-1111-1111-1111-111111111111",
    direction: "deliver",
    config: { path_template: "{collection}/{item_id}/{filename}" },
  };
  const res = await POST(makeCtx({ body, collectionId: "sensor-a" }));
  expect(res.status).toBe(201);
});
```
> Copy the exact mock setup + `makeCtx`/context helper from the neighbouring ingest-create test in the same file — do not invent a new harness.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd app && npx vitest run src/__tests__/api-associations.test.ts -t "delivery"`
Expected: FAIL — the `reference` guard or the create path rejects/branches incorrectly for `deliver`.

- [ ] **Step 3: Guard the reference check to ingest only**

In `app/src/pages/api/collections/[id]/connections/index.ts`, change the reference-mode block so it only runs for ingest configs (a delivery config has no `storage_mode`):
```ts
    // reference mode catalogs assets in place at the source, so it only makes
    // sense for an object-store source (§5.1). Ingest-only knob.
    if (
      data.direction === "ingest" &&
      data.config.storage_mode === "reference" &&
      connection.protocol !== "s3"
    ) {
      return jsonResponse(400, {
        error: "storage_mode 'reference' requires an object-store (s3) connection",
      });
    }
```
The rest of the handler (`createAssociation` with `direction: data.direction`) already works for both directions.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd app && npx vitest run src/__tests__/api-associations.test.ts`
Expected: PASS (delivery-create + all pre-existing ingest cases).

- [ ] **Step 5: Commit**

```bash
git add "app/src/pages/api/collections/[id]/connections/index.ts" app/src/__tests__/api-associations.test.ts
git commit -m "feat(api): accept delivery association create (reference check now ingest-only)"
```

---

## Task 9: Live end-to-end verification (lead-only)

Proves the outbox → dispatcher skeleton path against real infrastructure. Not a unit test — run the full stack.

**Files:** none (verification only). Capture results for the ROADMAP/ISSUES update in Task 10.

- [ ] **Step 1: Full verify gate**

Run:
```bash
npm run verify                       # repo root — app build + vitest
cd services/pipeline && uv run pytest && uv run ruff check
```
Expected: all pass, ruff clean.

- [ ] **Step 2: Bring up the stack and apply migration 007**

```bash
docker compose up -d --build
curl -s http://localhost:4321/api/collections/ >/dev/null   # trigger middleware migration
docker compose exec pgstac psql -U username -d postgis -c \
  "SELECT tgname FROM pg_trigger WHERE tgname = 'item_events_capture_trg';"
```
Expected: the trigger exists.

- [ ] **Step 3: Create a delivery association via the API**

Against a built-in-catalog collection (e.g. one already seeded, or create a test collection + an s3 connection first), POST a delivery association:
```bash
curl -sS -X POST http://localhost:4321/api/collections/<collection>/connections \
  -H 'content-type: application/json' \
  -d '{"connection_id":"<conn-uuid>","direction":"deliver","config":{"path_template":"{collection}/{item_id}/{filename}"}}'
```
Expected: `201` with the association JSON.

- [ ] **Step 4: Upsert an item and observe the dispatch match in the logs**

Trigger an item write into that collection (manual item create in the UI, or an ingest run, or `pypgstac`). Within one poll minute, check the pipeline logs:
```bash
docker compose logs pipeline --since 2m | grep -i "dispatch match"
```
Expected: a `dispatch match (skeleton — no transfer yet)` line naming the association id, item id, and asset keys. Confirm the outbox drained:
```bash
docker compose exec pgstac psql -U username -d postgis -c \
  "SELECT count(*) FILTER (WHERE processed_at IS NULL) AS pending FROM stac_higher.item_events;"
```
Expected: `pending = 0` after the tick.

- [ ] **Step 5: Confirm idempotency (no duplicate matches on the next tick)**

Wait one more minute; re-grep the logs. Expected: NO new match line for the same item (the outbox row is now `processed`), proving once-only consumption.

- [ ] **Step 6: Record results**

Note the verified facts (trigger fires, exactly one outbox row per upsert, dispatcher matched once and drained) for Task 10.

---

## Task 10: Docs — ROADMAP, ISSUES, FEATURES + merge

**Files:**
- Modify: `ROADMAP.md` (Phase 5 status line + Slice A note)
- Modify: `docs/ISSUES.md` (log the deferrals as tracked entries)
- Modify: `docs/FEATURES.md` (Phase 5 Slice A entry point)

- [ ] **Step 1: Update ROADMAP Phase 5**

In the status table (row "5–8"), split Phase 5 to `🚧 In progress` with a Slice A note mirroring the Phase 4 style: outbox migration 007 + ADR 0007 (trigger mechanism chosen in the A0 spike), dispatcher skeleton (poll-driven, cql2 match, logs pairs), delivery config contract (Zod + Python mirror), live-verified end-to-end. Update the Phase 5 detail section (§9) to mark Slice A done and list B/C/D remaining.

- [ ] **Step 2: Log deferrals in ISSUES.md**

Add tracked entries (next free I-numbers):
- `item_events` / `delivery_log` partitioning deferred to Phase 6 (mirrors I-11).
- Finalize-gating seam deferred to Phase 7 (dispatcher comment marks the spot).
- Dispatcher HA / leader election deferred to Phase 8 (§10 scheduler-HA) — Slice C will document the single-instance assumption.
- Item-visibility race in `dispatch_once` (item not yet in pgstac when the outbox row is claimed) — currently best-effort skip; revisit under Slice C's LISTEN loop.

- [ ] **Step 3: Add the FEATURES.md Phase 5 entry**

Add a Phase 5 section with Slice A entry points: `stac_higher.item_events` + trigger (migration 007), `pipeline/dispatcher/`, `pipeline/delivery/config.py` + `matcher.py`, the delivery Zod schema, ADR 0007.

- [ ] **Step 4: Commit the docs**

```bash
git add ROADMAP.md docs/ISSUES.md docs/FEATURES.md
git commit -m "docs(phase5): Slice A done — outbox + dispatcher skeleton live-verified"
```

- [ ] **Step 5: Merge Slice A to `ai/main`**

Run:
```bash
git checkout ai/main
git merge ai/slice-a --no-ff -m "Merge Slice A: Phase 5 event outbox + dispatcher skeleton"
npm run verify
cd services/pipeline && uv run pytest
```
Expected: verify + pytest pass on `ai/main`. Then clean up:
```bash
git worktree remove .claude/worktrees/slice-a
git branch -d ai/slice-a
```
(Do NOT push `ai/main` to origin — user keeps it local-only.)

---

## Self-Review

**Spec coverage:** Slice A of the spec maps to tasks — A0 spike (Task 1), migration 007 + ADR 0007 (Task 2), delivery config Zod + Python mirror (Tasks 3, 4), dispatcher outbox consumer + matcher + cql2 + asset filter (Tasks 5, 6), poll-driven wiring (Task 7), delivery-create route lift (Task 8), verify (Task 9), docs + deferrals + merge (Task 10). Byte transfer, `delivery_log`, retry/dead-letter, NOTIFY-woken loop, backfill, and the delivery UI are correctly OUT of Slice A (Slices B/C/D).

**Placeholder scan:** the one deliberate variable, `<TRIGGER-MECHANISM>` in Task 1, is resolved by the spike and Task 2 ships concrete SQL for the default (row-level) choice with an explicit swap note. `<collection>`/`<conn-uuid>` in Task 9 are live runtime values, not code placeholders. No TODO/TBD in shipped code.

**Type consistency:** `DeliverAssociation`, `Match`, `ItemEvent`, `DispatchRepo`, `dispatch_once`, `match_item`, `parse_delivery_config`, `DeliveryConfig` are defined once (Tasks 4–6) and referenced consistently in Tasks 6, 7, and the fake. Zod `deliveryConfigSchema` field names match `delivery/config.py` field names (`path_template`, `item_filter`, `asset_keys`, `payload{item_json,checksums,completion_marker}`, `on_update`, `overwrite`, `retry{max_attempts,backoff}`, `max_concurrent_transfers`).
