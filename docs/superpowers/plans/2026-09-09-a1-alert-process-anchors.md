# A-1 · I-84: `/api/alerts` gains `process_id` + `source_id` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Process-anchored alerts (`process_stalled` / `process_failed` / `process_rate_limited`) become attributable client-side, so the home overview, the product Overview, the process pages and the pipeline graph stop saying "unknown" or "may relate" about them.

**Architecture:** One additive change to the `ApiAlert` shape — `process_id` (the EFFECTIVE process: the alert's own, else its source's parent, the same COALESCE-at-read-time shape `collection_id` already uses) and `source_id` (raw). Migration 024 already stores both columns and `ALERT_SELECT` already joins `process_sources`/`processes`; only the projection and the mapper change. Then three client derivations consume it: `buildProductRows` claims process alerts through the product's wired processes and colours process lineage nodes; `unhealthyNodeIds` indicts `proc:<id>` nodes; `processVerdict` takes the open alert list and lets a process alert outrank the run ledger. The three lossy workarounds I-84 names are removed.

**Tech Stack:** Astro 7 API routes (node), TypeScript, React 19, TanStack Query, vitest.

**Spec:** `docs/ISSUES.md` I-84; ADR 0010 (alerting model — only NEW rows notify; an open alert row IS the verdict); the orchestrator brief "Lane A".

## Global Constraints

- **Worktree:** `.claude/worktrees/a1-alert-anchors`, branch `ai/a1-alert-anchors` off `ai/main` (created; `npm install` done).
- **Gate:** `npm run verify` from the worktree root must be green before each commit. Single file: `cd app && npx vitest run src/__tests__/<file>`. No e2e, no dev server, no Docker for the teammate — the lead does the live check in Chrome after merge.
- **Additive API change only.** No field is renamed or removed from `ApiAlert`; the two new fields are `string | null`. **No migration** — 024 already has `alerts.process_id` and `alerts.source_id`.
- **No contract fixture change.** `tests/contract-fixtures/alert-kinds.json` pins the `kind` enum only; the alert ROW shape is app-only (the pipeline writes rows, the app reads them). Do not add a fixture.
- **Health verdict rule (overview.ts header, ADR 0010):** an open alert row IS the verdict — firing ⇒ `error`, acknowledged ⇒ `warn`; the client never re-derives a condition the monitor owns.
- **Imports:** app code imports shared code from `@stac-higher/shared`; `@/…` for app-local. Never edit `components/ui/`. No new dependencies. The `astro check` PostToolUse hook runs after every `.ts`/`.tsx` edit — fix what it reports.
- **Route gating unchanged:** `/api/alerts` is a read; nothing in `authz/permissions.ts` changes.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL>
  ```

---

### Task 1: The API shape — `process_id` + `source_id` on `ApiAlert`

**Files:**
- Modify: `app/src/lib/alerts/storage.ts` — `AlertRow` (after `collection_id: string | null;`), `ApiAlert` (after the `collection_id` doc comment + field), `ALERT_SELECT` (the projection, after the `COALESCE(a.collection_id, cc.collection_id) AS collection_id` line), `toApiAlert` (after `collection_id: row.collection_id,`)
- Modify: `app/src/__tests__/alerts-storage.test.ts` — `dbRow` gains the two fields; one new assertion
- Modify: `app/src/__tests__/api-alerts.test.ts` — the `alert()` helper gains the two fields (the `ApiAlert` type now requires them)
- Modify: `app/src/__tests__/overview.test.ts` — the `alert()` helper gains `process_id: null, source_id: null` (type completeness only; the behaviour tests come in Task 2)

**Interfaces:**
- Produces:
  ```ts
  // app/src/lib/alerts/storage.ts — ApiAlert gains:
  /** The EFFECTIVE process for a process-anchored alert: the alert's own
   * `process_id`, else the parent of its `source_id` (`process_stalled`
   * anchors on a SOURCE — I-63). Same read-time COALESCE shape as
   * `collection_id`. Null for every non-process kind. (I-84) */
  process_id: string | null;
  /** The raw `process_sources.id` anchor, when the alert has one. */
  source_id: string | null;
  ```
  Exported through `app/src/lib/monitoring/api.ts` unchanged (`export type { ApiAlert as Alert }`), so every client `Alert` sees the fields.

- [ ] **Step 1: Write the failing storage test**

In `app/src/__tests__/alerts-storage.test.ts`, add to `dbRow` after `collection_id: null,`:

```ts
  process_id: "3a9f1c2e-0000-4000-8000-0000000000p1",
  source_id: "3a9f1c2e-0000-4000-8000-0000000000s1",
```

and inside `describe("listAlerts", …)` add:

```ts
  it("returns the effective process and the raw source anchor (I-84)", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never);
    const [row] = await listAlerts(["g1"]);
    expect(row.process_id).toBe("3a9f1c2e-0000-4000-8000-0000000000p1");
    expect(row.source_id).toBe("3a9f1c2e-0000-4000-8000-0000000000s1");
    // The projection COALESCEs the alert's own process with its source's
    // parent — the mapper must not re-derive it.
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toMatch(/COALESCE\(a\.process_id, ps\.process_id\) AS process_id/);
    expect(sql).toMatch(/a\.source_id/);
  });
```

(Adapt the `listAlerts(...)` call's arguments to the signature the existing tests in that file use — copy the first test's call exactly.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd app && npx vitest run src/__tests__/alerts-storage.test.ts`
Expected: FAIL — `row.process_id` is `undefined`.

- [ ] **Step 3: Implement**

In `app/src/lib/alerts/storage.ts`:

`AlertRow` — after `collection_id: string | null;` add:
```ts
  process_id: string | null;
  source_id: string | null;
```

`ApiAlert` — after the `collection_id: string | null;` field add:
```ts
  /** The EFFECTIVE process for a process-anchored alert: the alert's own
   * `process_id`, else the parent of its `source_id` (`process_stalled`
   * anchors on a SOURCE — I-63). Same read-time COALESCE shape as
   * `collection_id`. Null for every non-process kind. (I-84) */
  process_id: string | null;
  /** The raw `process_sources.id` anchor, when the alert has one. */
  source_id: string | null;
```

`ALERT_SELECT` — after the line `COALESCE(a.collection_id, cc.collection_id) AS collection_id` add a comma to that line and then:
```sql
         COALESCE(a.process_id, ps.process_id) AS process_id,
         a.source_id
```
(the `ps` join already exists below in the same statement — do not add a second one).

`toApiAlert` — after `collection_id: row.collection_id,` add:
```ts
    process_id: row.process_id,
    source_id: row.source_id,
```

- [ ] **Step 4: Fix the two helpers the type now rejects**

`app/src/__tests__/api-alerts.test.ts` `alert()` — after `collection_id: null,` add `process_id: null,` and `source_id: null,`.
`app/src/__tests__/overview.test.ts` `alert()` — same two lines after `collection_id: null,`.

- [ ] **Step 5: Verify and commit**

Run: `cd app && npx vitest run src/__tests__/alerts-storage.test.ts src/__tests__/api-alerts.test.ts src/__tests__/overview.test.ts` → all pass. Then `npm run verify` from the worktree root → green.

```bash
git add app/src/lib/alerts/storage.ts app/src/__tests__/alerts-storage.test.ts app/src/__tests__/api-alerts.test.ts app/src/__tests__/overview.test.ts
git commit -m "feat(alerts): /api/alerts returns process_id (effective) and source_id (I-84)"
```

---

### Task 2: Attribute process alerts to products — `buildProductRows`, home, product Overview

**Files:**
- Modify: `app/src/components/layout/overview.ts` — `ProductRollup.unattributed` doc comment; the `own` filter inside `buildProductRows`; the process-lineage `nodes.map` (health); delete `unanchoredAlerts` and its doc comment (end of file)
- Modify: `app/src/components/layout/DashboardPage.tsx` — the residual card's copy (anchor: `"— process alerts carry no product anchor in the alerts API."`)
- Modify: `app/src/components/collections/ProductOverview.tsx` — remove the `unanchoredAlerts` import, `processCount`, `maybeMine` and the `{maybeMine.length > 0 && (...)}` card; drop any now-unused imports (`AlertTriangle`, `ArrowRight`, `Card`, `CardContent`) ONLY if nothing else in the file uses them
- Test: `app/src/__tests__/overview.test.ts` — replace the `unanchoredAlerts` describe; extend attribution + lineage tests

**Interfaces:**
- Consumes: `Alert.process_id` from Task 1.
- Produces: `buildProductRows` claims an alert when `alert.process_id` names a process wired to the collection by a `process_source` (collection → `proc:<id>`) or `process_output` (`proc:<id>` → collection) edge; process lineage nodes carry `health` from that alert (firing `error`, acknowledged `warn`), else `warn` when undeployed, else `ok`. `unattributed` is the RESIDUAL — alerts no product could claim (channel-anchored `webhook_failed`, a process wired to no product).

- [ ] **Step 1: Write the failing tests**

In `app/src/__tests__/overview.test.ts`:

Extend `GRAPH.edges` with a process_output edge so both directions are covered:
```ts
    { from: "proc:p1", to: "coll:prod-a", kind: "process_output", id: "o1" },
```
(Check the existing "groups sources, processes and destinations with their labels" test: it counts process nodes — after this change the product has TWO process lineage nodes for `p1` (one per edge), so update that test's expectation to the labels it now yields, e.g. `["masker", "masker"]` with details `"reads this product"` and `"writes this product"`. Adjust exactly that assertion, nothing else.)

Replace the test `"surfaces an alert no product can claim rather than dropping it"` with:

```ts
  it("claims a process alert through a wired process (I-84)", () => {
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [alert({ id: "proc-alert", kind: "process_failed", process_id: "p1" })],
    });
    expect(rows[0].health).toBe("error");
    expect(rows[0].reason).toBe(alertKindLabel("process_failed"));
    expect(unattributed).toEqual([]);
  });

  it("still surfaces an alert no product can claim — a channel alert, or a process wired to nothing", () => {
    const { rows, unattributed } = buildProductRows({
      collections: [{ id: "prod-a" }],
      graph: GRAPH,
      flows: [flow()],
      alertsAreComplete: true,
      openAlerts: [
        alert({ id: "webhook", kind: "webhook_failed", channel_id: "ch1" }),
        alert({ id: "elsewhere", kind: "process_stalled", process_id: "p-not-wired" }),
      ],
    });
    expect(rows[0].health).toBe("ok");
    expect(unattributed.map((a) => a.id)).toEqual(["webhook", "elsewhere"]);
  });
```
(import `alertKindLabel` from `@/components/monitoring/shared` at the top of the test file.)

In `describe("buildProductRows lineage and counts")` add:

```ts
  it("colours a process lineage node from its open alert — firing is an error, acknowledged a warning", () => {
    const firing = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: true,
      openAlerts: [alert({ id: "a", kind: "process_failed", process_id: "p1" })],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(firing.nodes.every((n) => n.health === "error")).toBe(true);
    expect(firing.health).toBe("error");

    const acked = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: true,
      openAlerts: [alert({ id: "a", kind: "process_failed", process_id: "p1", state: "acknowledged" })],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(acked.nodes.every((n) => n.health === "warn")).toBe(true);
  });

  it("a deployed process with no alert is ok, not unknown", () => {
    const group = buildProductRows({
      collections: [{ id: "prod-a" }], graph: GRAPH, flows: [flow()], alertsAreComplete: true, openAlerts: [],
    }).rows[0].lineage.find((g) => g.kind === "process")!;
    expect(group.nodes.every((n) => n.health === "ok")).toBe(true);
    expect(group.health).toBe("ok");
  });
```

Update the existing `"flags an undeployed process as a warning"` test only if its graph fixture needs `openAlerts: []` — its expectation (`warn`) is unchanged.

Delete the whole `describe("unanchoredAlerts", …)` block and the `unanchoredAlerts` import.

- [ ] **Step 2: Run to verify failure**

Run: `cd app && npx vitest run src/__tests__/overview.test.ts`
Expected: FAIL — the process alert lands in `unattributed`, process nodes have `health: undefined`, `unanchoredAlerts` import still resolves (delete test first, so the import error is the first failure — that is fine).

- [ ] **Step 3: Implement in `overview.ts`**

Replace the `ProductRollup.unattributed` doc comment with:
```ts
  /**
   * Open alerts no product could claim: channel-anchored `webhook_failed`
   * rows, or a process alert whose process is wired to no collection.
   * Since I-84 process alerts carry `process_id`, so this is a residual,
   * not a class — surfacing the count keeps this page and /monitoring
   * from disagreeing.
   */
```

In `buildProductRows`, after `const processes = edges.filter(...)` add:
```ts
    // Process ids wired to this product, either direction (I-84: a process
    // alert names its effective process, so it can be claimed here).
    const wiredProcesses = new Set(
      processes.map((e) =>
        (e.kind === "process_source" ? e.to : e.from).replace(/^proc:/, ""),
      ),
    );
```
and extend the `own` filter's `mine` expression with a fourth clause:
```ts
        (!!a.connection_id && wiredConnections.has(a.connection_id)) ||
        (!!a.process_id && wiredProcesses.has(a.process_id));
```

Replace the process-lineage node body (the `nodes: processes.map((e) => { ... })` block) with:
```ts
        nodes: processes.map((e) => {
          const id = e.kind === "process_source" ? e.to : e.from;
          const node = nodesById.get(id);
          const processId = id.replace(/^proc:/, "");
          const undeployed = node?.meta.deployed === false;
          // The monitor's open alert IS the verdict (ADR 0010); deployment
          // state is the graph's own signal beneath it.
          const alert =
            alerts.find((a) => a.process_id === processId && a.state === "firing") ??
            alerts.find((a) => a.process_id === processId);
          const health: LineageHealth = alert
            ? alertHealth(alert)
            : undeployed
              ? "warn"
              : "ok";
          return {
            id: `${e.kind}:${e.id}`,
            label: node?.label ?? id,
            detail: undeployed
              ? "not deployed"
              : e.kind === "process_source"
                ? "reads this product"
                : "writes this product",
            href: `/processes/${processId}`,
            health,
          };
        }),
```

Delete the `unanchoredAlerts` function and its doc comment at the end of the file.

- [ ] **Step 4: The two consumers**

`DashboardPage.tsx` — change the residual card's trailing copy from
`{" — process alerts carry no product anchor in the alerts API."}` to
`{" — a channel alert, or a process wired to no product."}`.

`ProductOverview.tsx` — remove `unanchoredAlerts` from the `@/components/layout/overview` import; delete the `processCount` and `maybeMine` declarations and their comment; delete the `{maybeMine.length > 0 && (<Card …>…</Card>)}` block; remove imports that are now unused (check each of `AlertTriangle`, `ArrowRight`, `Card`, `CardContent` with a grep of the file before removing).

- [ ] **Step 5: Verify and commit**

Run: `cd app && npx vitest run src/__tests__/overview.test.ts` → pass; then `npm run verify` → green (the `astro check` must not report unused imports).

```bash
git add app/src/components/layout/overview.ts app/src/components/layout/DashboardPage.tsx app/src/components/collections/ProductOverview.tsx app/src/__tests__/overview.test.ts
git commit -m "feat(overview): attribute process alerts to products through wired processes; drop the two 'may relate' caveats (I-84)"
```

---

### Task 3: Process health from alerts — `processVerdict`, the graph, docs

**Files:**
- Modify: `app/src/components/processes/health.ts` — module doc (the "Process-anchored ALERTS are deliberately not consulted" paragraph), `processVerdict` signature + body
- Modify: `app/src/components/processes/ProcessesPage.tsx` — fetch `useAlerts("open")` once in the page component and pass `alerts` into the card component that calls `processVerdict` (add an `openAlerts?: Alert[]` prop to it)
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` — `DeployState` calls `useAlerts("open")` and passes the list
- Modify: `app/src/lib/monitoring/graph-decorate.ts` — module doc last sentence; `unhealthyNodeIds` (accept `process_id`); `nodeHealth` (process nodes are `ok` without an alert)
- Modify: `docs/ISSUES.md` — the I-84 entry → 🟢 resolved; `docs/FEATURES.md` — the alerts/monitoring row that describes `/api/alerts` (grep `process_id` or `ApiAlert`; if no row mentions the shape, append one sentence to the "Alerts" feature row)
- Test: `app/src/__tests__/processes-health.test.ts` (new), `app/src/__tests__/graph-decorate.test.ts` (new)

**Interfaces:**
- Consumes: `Alert.process_id` (Task 1).
- Produces:
  ```ts
  export function processVerdict(
    process: Process,
    runs: ProcessRun[] | undefined,
    sourceCount: number | undefined,
    openAlerts?: Alert[] | undefined,   // NEW, optional — omitted ⇒ ledger-only, as today
  ): ProcessVerdict;
  export function unhealthyNodeIds(alerts: { kind: string; connection_id?: string | null; collection_id?: string | null; process_id?: string | null }[]): Set<string>;
  ```

- [ ] **Step 1: Write the failing tests**

Create `app/src/__tests__/processes-health.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { processVerdict } from "@/components/processes/health";
import type { Alert } from "@/lib/monitoring/api";
import type { Process, ProcessRun } from "@/lib/processes/types";

const process = {
  id: "p1",
  enabled: true,
  current_revision: "r1",
} as unknown as Process;

function run(over: Partial<ProcessRun> = {}): ProcessRun {
  return {
    id: "run1", status: "succeeded", is_test: false, started_at: "2026-09-01T00:00:00Z",
    finished_at: "2026-09-01T00:01:00Z", rate_deferred_until: null, ...over,
  } as unknown as ProcessRun;
}

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a1", source: "flow", kind: "process_failed", connection_id: null, association_id: null,
    channel_id: null, state: "firing", message: "", first_seen: "", last_seen: "",
    acknowledged_at: null, acknowledged_by: null, resolved_at: null, group_id: null,
    connection_name: null, collection_id: null, process_id: "p1", source_id: null, ...over,
  } as Alert;
}

describe("processVerdict with alerts (I-84)", () => {
  it("is ledger-only when no alert list is given — unchanged behaviour", () => {
    expect(processVerdict(process, [run()], 1).health).toBe("ok");
  });

  it("a firing process alert is an error, labelled by its kind, even with a clean ledger", () => {
    const v = processVerdict(process, [run()], 1, [alert()]);
    expect(v.health).toBe("error");
    expect(v.label).toBe("Failing");
    expect(v.reason).toMatch(/failed/i);
  });

  it("an acknowledged alert is a warning", () => {
    const v = processVerdict(process, [run()], 1, [alert({ state: "acknowledged" })]);
    expect(v.health).toBe("warn");
    expect(v.reason).toMatch(/acknowledged/);
  });

  it("ignores alerts for other processes", () => {
    expect(processVerdict(process, [run()], 1, [alert({ process_id: "p2" })]).health).toBe("ok");
  });

  it("deployment state still comes first — a disabled process is unknown even when alerting", () => {
    const disabled = { ...process, enabled: false } as Process;
    expect(processVerdict(disabled, [run()], 1, [alert()]).health).toBe("unknown");
  });
});
```

Create `app/src/__tests__/graph-decorate.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { nodeHealth, unhealthyNodeIds } from "@/lib/monitoring/graph-decorate";
import type { GraphNode } from "@/lib/monitoring/graph-api";

const proc: GraphNode = { id: "proc:p1", type: "process", label: "masker", group_id: null, meta: { deployed: true } };

describe("unhealthyNodeIds", () => {
  it("indicts connection, collection and process nodes by their anchors", () => {
    const ids = unhealthyNodeIds([
      { kind: "connection_error", connection_id: "c1" },
      { kind: "push_rejected", collection_id: "prod-a" },
      { kind: "process_failed", process_id: "p1" },
    ]);
    expect([...ids].sort()).toEqual(["coll:prod-a", "conn:c1", "proc:p1"]);
  });
});

describe("nodeHealth", () => {
  it("a deployed process with no alert is ok (I-84 — was unknown)", () => {
    expect(nodeHealth(proc, new Set())).toBe("ok");
  });
  it("a process named by an alert is an error", () => {
    expect(nodeHealth(proc, new Set(["proc:p1"]))).toBe("error");
  });
  it("an undeployed process is still a warning", () => {
    expect(nodeHealth({ ...proc, meta: { deployed: false } }, new Set())).toBe("warn");
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd app && npx vitest run src/__tests__/processes-health.test.ts src/__tests__/graph-decorate.test.ts`
Expected: FAIL — `processVerdict` ignores the 4th argument (health `ok`), `nodeHealth` returns `unknown`, `unhealthyNodeIds` omits `proc:p1`.

- [ ] **Step 3: Implement `health.ts`**

Replace the module-doc paragraph beginning "Process-anchored ALERTS are deliberately not consulted" with:
```
 * Process-anchored ALERTS are consulted when the caller passes the open list
 * (I-84: `/api/alerts` returns the effective `process_id`). An open alert is
 * the monitor's verdict (ADR 0010) and outranks the ledger: firing ⇒ error,
 * acknowledged ⇒ warning. Deployment state still comes first — a disabled
 * process is "unknown" whatever the monitor says about its past.
```
Add the import `import type { Alert } from "@/lib/monitoring/api";` and `import { alertKindLabel } from "@/components/monitoring/shared";`.

Change the signature to `processVerdict(process, runs, sourceCount, openAlerts?: Alert[] | undefined)` and insert, after the `sourceCount === 0` return and BEFORE `const ledger = realRuns(runs);`:
```ts
  const own = (openAlerts ?? []).filter((a) => a.process_id === process.id);
  const firingAlert = own.find((a) => a.state === "firing");
  const acknowledgedAlert = own.find((a) => a.state === "acknowledged");
  if (firingAlert) {
    return { health: "error", label: "Failing", reason: alertKindLabel(firingAlert.kind) };
  }
  if (acknowledgedAlert) {
    return {
      health: "warn",
      label: "Degraded",
      reason: `${alertKindLabel(acknowledgedAlert.kind)} (acknowledged)`,
    };
  }
```

- [ ] **Step 4: Thread the alert list through the two pages**

`ProcessesPage.tsx`: in the page-level component that renders the cards, add `const { data: openAlerts } = useAlerts("open");` (import `useAlerts` from `@/lib/monitoring/queries`) and pass `openAlerts={openAlerts}` to each card; in the card component add `openAlerts?: Alert[]` to its props (import `type Alert` from `@/lib/monitoring/api`) and change the call to `processVerdict(process, runs, sources?.length, openAlerts)`. One fetch per page, not per card.

`ProcessDetailPage.tsx` `DeployState`: add `const { data: openAlerts } = useAlerts("open");` and pass it as the fourth argument.

- [ ] **Step 5: Implement `graph-decorate.ts`**

Module doc: replace the last two sentences ("Process-anchored alerts carry no id … rather than a guess.") with "Process-anchored alerts carry their effective `process_id` (I-84), so a process node is indicted the same way a connection or collection is."

`unhealthyNodeIds`: add `process_id?: string | null;` to the parameter type and `if (alert.process_id) ids.add(\`proc:${alert.process_id}\`);` to the loop.

`nodeHealth`: replace the last two lines (`// No client-readable process anchor…` and the ternary) with `return "ok";`.

- [ ] **Step 6: Docs**

`docs/ISSUES.md` — replace the I-84 heading and body with:
```markdown
### I-84 · `/api/alerts` omits `process_id` / `source_id` — 🟢 resolved (A-1, 2026-09-09)
`ApiAlert` gained `process_id` (the EFFECTIVE process — the alert's own, else
its source's parent, the same read-time COALESCE `collection_id` uses) and
`source_id` (raw). `buildProductRows` claims process alerts through the
product's wired processes and colours process lineage nodes from them;
`processVerdict` takes the open alert list and lets an open alert outrank the
run ledger; the pipeline graph indicts `proc:<id>` nodes and no longer paints
deployed processes `unknown`. The product Overview's "may relate to this
product" caveat is gone; the home page keeps its residual "not shown against
a product" line, which now covers only channel-anchored alerts and processes
wired to no product. No migration (024 already stored both columns); no
fixture (the row shape is app-only). Commit: <the Task 3 commit SHA>.
```
(Replace `<the Task 3 commit SHA>` after committing, in the same commit via `git commit --amend --no-edit` is NOT allowed — instead write "commit: see `git log --grep I-84`".)

`docs/FEATURES.md` — find the row describing alerts (`grep -n 'api/alerts' docs/FEATURES.md`) and append: "Since A-1 (2026-09-09) the shape carries `process_id` (effective) + `source_id`, so process alerts attribute to products, process pages and graph nodes (I-84)."

- [ ] **Step 7: Verify and commit**

Run: `npm run verify` → green.

```bash
git add app/src/components/processes/health.ts app/src/components/processes/ProcessesPage.tsx app/src/components/processes/ProcessDetailPage.tsx app/src/lib/monitoring/graph-decorate.ts app/src/__tests__/processes-health.test.ts app/src/__tests__/graph-decorate.test.ts docs/ISSUES.md docs/FEATURES.md
git commit -m "feat(processes,graph): process health from open alerts; graph indicts process nodes; close I-84"
```

---

### Task 4: Merge + live check (lead only)

- [ ] `npm run verify` on the branch; merge `--no-ff` into `ai/main`; verify again.
- [ ] Chrome, against the standing demo: `/`, a product Overview (`/collections/goes-geocolor`), `/graph`, `/processes` — confirm process nodes are green (or coloured by a real open process alert attributed to its product), the "may relate" card is gone, and the home residual line (if shown) names only channel/unwired alerts. Screenshots noted in the landed note.
- [ ] Tick A-1 in the orchestrator's ledger; TODO.md follow-up "A-1 landed".

## Self-review

**Spec coverage.** I-84: two fields added (T1); `processVerdict` folds alerts in (T3); the three workarounds — home count (T2: kept as a residual with honest copy — Ruling: it also covers channel alerts, which were never process-related, so deleting it would hide real orphans), product Overview caveat (T2: removed), graph `unknown` (T3: removed). `overview.test.ts` extended (T2). Fixture: none, justified in Global Constraints. ISSUES closed (T3). ✓

**Placeholder scan.** Each step carries code. The `listAlerts(...)` call in T1 Step 1 defers to the file's existing call signature by explicit instruction (copy the first test's call). The ISSUES commit-SHA placeholder is resolved by instruction.

**Type consistency.** `process_id: string | null` / `source_id: string | null` in `AlertRow`, `ApiAlert`, `toApiAlert`, and every test helper. `processVerdict`'s 4th parameter is `Alert[] | undefined` in health.ts, the card prop, and both call sites. `unhealthyNodeIds`'s structural parameter gains `process_id?: string | null`, matching what `ApiAlert` provides.
