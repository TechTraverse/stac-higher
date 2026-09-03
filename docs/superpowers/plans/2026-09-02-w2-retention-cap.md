# W-2 · Retention Count Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A collection can declare "keep at most N items", and the existing retention sweep expires everything beyond the newest N — through the same mark-then-collect queue every other deletion already uses.

**Architecture:** One nullable column on `collection_settings`, one widened predicate in the query that finds collections with GC work, and one extra branch in the query that lists a collection's expired items. Everything downstream — marking the asset prefix, the grace window, the collector — is untouched, because a count cap is a retention policy and not a new way to delete. The write path (settings schema, route, Settings tab, impact dry-run) follows the shape `retention_days` already has, so there is no new UI pattern either.

**Tech Stack:** TypeScript migration + Zod + React (app), Python/psycopg (pipeline), vitest + pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-ingest-window-and-retention-cap-design.md` §4.

## Global Constraints

- Worktree off `ai/main`: `git worktree add .claude/worktrees/w2-retention -b ai/w2-retention ai/main`; `npm install` at the worktree root; `cd services/pipeline && uv sync --extra dev`.
- Gates: `npm run verify` (repo root) and, from `services/pipeline/`, `uv run pytest` + `uv run ruff check .`.
- **The app owns the DDL** (ADR 0001). The column arrives in an app migration; the pipeline only reads it.
- **Migration number: 026**, per the spec's §8 note. The K queue's K-3 slice also planned 026 and has not started, so K-3's spec must be bumped to 027 — do that edit as part of Task 1 rather than leaving two specs claiming one number.
- `collection_settings` is **not** a cross-runtime jsonb contract: the pipeline reads typed columns, so there is no golden fixture (the settings schema file says so in its header — keep that true).
- **ADR 0011 is not being amended.** Mark-first-then-collect, the `retention` reason and `gc_grace_days` all stay exactly as they are. If an implementation step seems to need a new `asset_gc` reason, stop — reuse `retention`, and say why in the report.
- Never edit `app/src/components/ui/*` (hook-blocked). The `astro check` hook runs after `.ts`/`.tsx` edits.
- Commit messages end with the session's attribution trailer.

---

### Task 1: Migration 026 — the column

**Files:**
- Modify: `app/src/lib/db/migrate.ts` (append after `025_process_runs_queued_source_idx`)
- Modify: `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` (K-3's migration 026 → 027)
- Modify: `TODO.md` (K-3's slice text, same renumber)
- Test: `app/src/__tests__/` — a new `retention-cap-migration.test.ts` in the style of `process-runs-coalescing-migration.test.ts`

**Interfaces:**
- Produces: `stac_higher.collection_settings.retention_max_items integer`, nullable, `CHECK (retention_max_items IS NULL OR retention_max_items >= 1)`.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/retention-cap-migration.test.ts`:

```ts
// @vitest-environment node
/**
 * Migration 026 shape (W-2).
 *
 * The column is nullable with a >= 1 CHECK because "keep at most zero items"
 * is not a retention policy, it is `archived` — which already exists and has
 * different semantics (it expires everything and blocks writes).
 */
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const sql = migrationEntry("026_collection_settings_retention_max_items");

describe("migration 026 (retention count cap)", () => {
  it("exists, after 025", () => {
    expect(
      migrationSource.indexOf('"026_collection_settings_retention_max_items"'),
    ).toBeGreaterThan(
      migrationSource.indexOf('"025_process_runs_queued_source_idx"'),
    );
  });

  it("adds a nullable column to collection_settings", () => {
    expect(sql).toContain("ALTER TABLE stac_higher.collection_settings");
    expect(sql).toContain("ADD COLUMN IF NOT EXISTS retention_max_items integer");
    expect(sql).not.toContain("NOT NULL");
  });

  it("refuses a cap below one", () => {
    expect(sql).toMatch(/CHECK\s*\(\s*retention_max_items IS NULL OR retention_max_items >= 1\s*\)/);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd app && npx vitest run src/__tests__/retention-cap-migration.test.ts`
Expected: FAIL — `migrationEntry` throws for the unknown name.

- [ ] **Step 3: Implement**

Append to `MIGRATIONS` in `app/src/lib/db/migrate.ts`:

```ts
  {
    // W-2 (spec §4): a COUNT cap beside the age cap. Both are retention, both
    // feed ADR 0011's single mark-then-collect queue, so this adds a rule to
    // an existing sweep rather than a new way to delete.
    //
    // Nullable = no cap. The CHECK floors it at 1 because "keep zero" is not
    // a retention policy — that is `archived`, which already exists and also
    // blocks writes.
    name: "026_collection_settings_retention_max_items",
    sql: `
      ALTER TABLE stac_higher.collection_settings
        ADD COLUMN IF NOT EXISTS retention_max_items integer;

      ALTER TABLE stac_higher.collection_settings
        DROP CONSTRAINT IF EXISTS collection_settings_retention_max_items_check;
      ALTER TABLE stac_higher.collection_settings
        ADD CONSTRAINT collection_settings_retention_max_items_check
        CHECK (retention_max_items IS NULL OR retention_max_items >= 1);
    `,
  },
```

- [ ] **Step 4: Renumber K-3**

In `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` and in `TODO.md`'s K-3 entry, change K-3's "Migration **026**" to **027**, with a short parenthetical: `(026 taken by W-2's retention cap, which landed first)`. Grep both files for `026` to be sure nothing else refers to it.

- [ ] **Step 5: Run, typecheck, commit**

Run: `cd app && npx vitest run src/__tests__/retention-cap-migration.test.ts && npm run check`

```bash
git add app/src/lib/db/migrate.ts app/src/__tests__/retention-cap-migration.test.ts docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md TODO.md
git commit -m "feat(db): migration 026 — collection_settings.retention_max_items (W-2)"
```

---

### Task 2: The sweep expires beyond the newest N

**Files:**
- Modify: `services/pipeline/src/pipeline/gc/repo.py` (`GcCollection`, `list_gc_collections`, `list_expired_items`)
- Test: `services/pipeline/tests/` — find the GC suite (`grep -l "retention_tick\|list_expired_items" services/pipeline/tests/*.py`)

**Interfaces:**
- `GcCollection` gains `retention_max_items: int | None = None`.
- `list_gc_collections` selects the new column and widens its predicate to `WHERE retention_days IS NOT NULL OR archived = true OR retention_max_items IS NOT NULL`.
- `list_expired_items(self, collection_id: str, retention_days: int | None, limit: int, *, retention_max_items: int | None = None) -> list[str]` — keyword-only and defaulted, so the existing call sites and any fake keep working until updated.

- [ ] **Step 1: Write the failing tests**

The GC suite drives a fake repo, so the count logic needs a test at the SQL-assembly level plus behaviour tests through `retention_tick`. Add to the GC test module:

```python
# --- count cap (W-2) ------------------------------------------------------- #


def test_gc_collection_carries_the_count_cap():
    from pipeline.gc.repo import GcCollection

    c = GcCollection(
        collection_id="c", retention_days=None, gc_grace_days=3, archived=False
    )
    assert c.retention_max_items is None


async def test_a_collection_with_only_a_count_cap_is_swept():
    """Before W-2 the sweep only visited collections with retention_days or
    archived, so a count-capped collection would never be looked at."""
    repo = FakeGcRepo(
        collections=[
            GcCollection(
                collection_id="c",
                retention_days=None,
                gc_grace_days=3,
                archived=False,
                retention_max_items=2,
            )
        ],
        expired={"c": ["old-1"]},
    )
    result = await retention_tick(repo, batch_limit=100)
    assert result.collections == 1
    assert result.expired_items == 1


async def test_the_cap_is_passed_to_the_expired_query():
    repo = FakeGcRepo(
        collections=[
            GcCollection(
                collection_id="c",
                retention_days=7,
                gc_grace_days=3,
                archived=False,
                retention_max_items=24,
            )
        ],
        expired={"c": []},
    )
    await retention_tick(repo, batch_limit=100)
    assert repo.expired_calls == [("c", 7, 100, 24)]


async def test_archived_ignores_the_count_cap():
    """Archived means empty the collection; keeping the newest N would be the
    opposite of what the operator asked for (ADR 0009)."""
    repo = FakeGcRepo(
        collections=[
            GcCollection(
                collection_id="c",
                retention_days=None,
                gc_grace_days=3,
                archived=True,
                retention_max_items=24,
            )
        ],
        expired={"c": []},
    )
    await retention_tick(repo, batch_limit=100)
    assert repo.expired_calls == [("c", None, 100, None)]
```

Extend the suite's fake repo to record `expired_calls` as `(collection_id, retention_days, limit, retention_max_items)` and to accept the keyword. Read the existing fake first — reuse its constructor style rather than inventing one.

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/pipeline && uv run pytest -k "count cap or count_cap or archived_ignores" -q`

- [ ] **Step 3: Implement the repo**

`GcCollection` gains the field. `list_gc_collections`:

```python
                "SELECT collection_id, retention_days, gc_grace_days, archived,"
                "       retention_max_items"
                "  FROM stac_higher.collection_settings"
                " WHERE retention_days IS NOT NULL OR archived = true"
                "    OR retention_max_items IS NOT NULL",
```

keeping the existing `retention_days=None if r[3] else r[1]` mapping (archived forces "all items") and adding `retention_max_items=None if r[3] else r[4]` — the archived override lives in ONE place, here, so the sweep and the query below cannot disagree about it.

`list_expired_items` assembles only the branches that apply:

```python
    async def list_expired_items(  # pragma: no cover
        self,
        collection_id: str,
        retention_days: int | None,
        limit: int,
        *,
        retention_max_items: int | None = None,
    ) -> list[str]:
        """Item ids this collection's retention rules have expired.

        Two rules, UNIONed: older than `retention_days`, and beyond the newest
        `retention_max_items`. `retention_days=None` still means ALL items —
        that is how `list_gc_collections` expresses `archived`, and archiving
        also clears the count cap so the two cannot contradict each other.

        The SQL is assembled rather than parameterised over NULLs because
        `OFFSET NULL` is an error, not a no-op.
        """
        async with await self._connect() as conn:
            try:
                if retention_days is None and retention_max_items is None:
                    # archived, or a settings row with no rule at all
                    cur = await conn.execute(
                        "SELECT id FROM pgstac.items WHERE collection = %s"
                        " ORDER BY id LIMIT %s",
                        (collection_id, limit),
                    )
                else:
                    branches: list[str] = []
                    params: list[Any] = []
                    if retention_days is not None:
                        branches.append(
                            "SELECT id FROM pgstac.items"
                            " WHERE collection = %s"
                            "   AND datetime < now() - make_interval(days => %s)"
                        )
                        params += [collection_id, retention_days]
                    if retention_max_items is not None:
                        # Everything past the newest N. The id tiebreak makes
                        # OFFSET deterministic when datetimes collide — without
                        # it the sweep could mark a different arbitrary subset
                        # each tick.
                        branches.append(
                            "SELECT id FROM ("
                            "  SELECT id FROM pgstac.items WHERE collection = %s"
                            "   ORDER BY datetime DESC, id DESC OFFSET %s"
                            ") beyond_cap"
                        )
                        params += [collection_id, retention_max_items]
                    sql = (
                        " UNION ".join(branches)
                        + " ORDER BY id LIMIT %s"
                    )
                    params.append(limit)
                    cur = await conn.execute(sql, tuple(params))
                rows = await cur.fetchall()
            except Exception:
                return []
        return [str(r[0]) for r in rows]
```

`sweep.py`'s `retention_tick` passes it through:

```python
        items = await repo.list_expired_items(
            c.collection_id,
            c.retention_days,
            batch_limit,
            retention_max_items=c.retention_max_items,
        )
```

The `reason` line is unchanged: `REASON_ARCHIVE if c.archived else REASON_RETENTION`.

- [ ] **Step 4: Run, lint, commit**

Run: `cd services/pipeline && uv run pytest -q && uv run ruff check .`

```bash
git add services/pipeline/src/pipeline/gc/repo.py services/pipeline/src/pipeline/gc/sweep.py services/pipeline/tests/
git commit -m "feat(gc): expire items beyond the newest N (W-2)"
```

- [ ] **Step 5: Verify the SQL against a real database**

The repo methods are `# pragma: no cover` and the unit tests drive a fake, so the assembled SQL is unproven. With the stack up, check both branches by hand:

```bash
docker compose exec -T database psql -U username -d postgis -c "
  SELECT id FROM (
    SELECT id FROM pgstac.items WHERE collection = 'demo-scenes'
     ORDER BY datetime DESC, id DESC OFFSET 0
  ) beyond_cap ORDER BY id LIMIT 5;"
```

Expected: it runs and returns rows (offset 0 = everything). Then repeat with an offset above the item count and expect zero rows. Report the output; a syntax error here is the whole point of the step.

---

### Task 3: The write path — schema, route, settings storage

**Files:**
- Modify: `app/src/lib/collections/settings-schemas.ts`
- Modify: `app/src/lib/collections/settings.ts` (`CollectionSettings`, the SELECT, the UPSERT)
- Modify: `app/src/pages/api/collections/[id]/settings/impact.ts`
- Test: the existing settings tests (`grep -l "collectionSettingsUpdateSchema\|settings/impact" app/src/__tests__/*.ts`)

**Interfaces:**
- `collectionSettingsUpdateSchema` gains `retention_max_items: z.number().int().min(1).max(1_000_000).nullable()`.
- `CollectionSettings` gains `retentionMaxItems: number | null`; the SELECT and the UPSERT carry the column.
- The impact route accepts `?retention_max_items=N` alongside `?retention_days=N`; when both are present it answers with the union, matching what the sweep would do.

- [ ] **Step 1: Failing tests** — schema accepts null and a positive int, rejects 0 and non-integers; a round-trip through `getCollectionSettings` / update returns the value; the impact route with `?retention_max_items=2` against a collection of 5 items reports `expired_items: 3`, and with both parameters reports the union rather than the sum. Read the existing impact test for how it seeds items.

- [ ] **Step 2: Implement.** The impact query for the count branch mirrors the sweep's:

```sql
SELECT count(*) FROM (
  SELECT id FROM pgstac.items WHERE collection = $1
   ORDER BY datetime DESC, id DESC OFFSET $2
) beyond_cap
```

For the union case, count `DISTINCT id` over both branches so an item that is both old and beyond the cap is not counted twice — the number shown in the warn-and-proceed dialog must equal what actually gets deleted.

- [ ] **Step 3: Run, typecheck, commit**

```bash
git add app/src/lib/collections/ "app/src/pages/api/collections/[id]/settings/impact.ts" app/src/__tests__/
git commit -m "feat(settings): retention_max_items write path and impact dry-run (W-2)"
```

---

### Task 4: The Settings tab control

**Files:**
- Modify: `app/src/components/collections/SettingsTab.tsx`
- Test: `app/src/__tests__/settings-tab.test.tsx`

**Interfaces:**
- A `Maximum items` input beside `Retention (days)`, empty string = no cap, same parse/validate shape as `retentionDays` (`retentionMaxItems` state, `retentionMaxParsed`, `retentionMaxInvalid`).
- The existing warn-and-proceed dialog triggers when the cap is newly set **or lowered**, the same rule `retentionTightened` already applies to days.

- [ ] **Step 1: Failing test** — set a cap, save, assert the payload carries `retention_max_items`; lower an existing cap and assert the confirmation dialog appears with the impact count; clear it and assert `null` is sent. Follow `settings-tab.test.tsx`'s existing mocking of `useCollectionSettings` / `useUpdateCollectionSettings`.

- [ ] **Step 2: Implement**, extending `retentionTightened` to cover the cap:

```ts
  const capTightened =
    retentionMaxParsed !== null &&
    (settings?.retentionMaxItems === null ||
      retentionMaxParsed < (settings?.retentionMaxItems ?? Infinity));
```

Help text, one sentence: keeps the newest N items by observation time; older ones are deleted after the grace period, the same as the day-based rule.

- [ ] **Step 3: Run, typecheck, commit**

```bash
git add app/src/components/collections/SettingsTab.tsx app/src/__tests__/settings-tab.test.tsx
git commit -m "feat(settings-ui): maximum-items control on the Settings tab (W-2)"
```

---

### Task 5: Docs and verification

**Files:**
- Modify: `docs/FEATURES.md` (retention/GC row), `docs/decisions/README.md` (a line under ADR 0011 noting retention now has two rules feeding one queue — the ADR itself is not amended), the spec's status line

- [ ] **Step 1:** Document the rule as "keep the newest N by item datetime", the union with `retention_days`, that `archived` overrides both, and that nothing about the grace window or the collector changed.

- [ ] **Step 2: Full verification**

Run from the worktree root: `npm run verify`
Run from `services/pipeline/`: `uv run pytest && uv run ruff check .`

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs(retention): the count cap alongside the age rule (W-2)"
```

**Lead-only, needs Docker:** on the demo collection, set `retention_max_items: 1`, seed a second scene, and watch the sweep mark and then collect the older one after the grace window. `pipeline.demo status` shows the item counts; `stac_higher.asset_gc` shows the mark with reason `retention`.
