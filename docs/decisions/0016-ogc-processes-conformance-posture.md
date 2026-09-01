# ADR 0016 — OGC API — Processes conformance posture

- **Status:** accepted (2026-08-31 — direction set by the lead: future OGC
  API — Processes conformance is a goal wherever it is achievable without
  contorting the internal model; where a part cannot be claimed honestly,
  the reasoning is documented instead. Extends — does not reopen — the
  I-66/§11 settlement that the facade is a post-gate stretch slice.)
- **Owners:** the future facade slice (unscheduled — post-M3, lead
  green-light per the Phase 9 spec §11); constraints below bind the
  `container`-runtime slice (ADR 0013) and process-schema evolution now.
- **Related:** ISSUES I-66 (settled) / I-81 (cancel verb); Phase 9 design
  spec §11; ADR 0013 (executor), ADR 0014 (output path).

## Context

The I-66 decision (2026-08-29) evaluated the facade against **OGC API —
Processes Part 1 as approved (v1.0, OGC 18-062r2)**: read-only process
list/describe + async execute + job status/results, with conformance
candidates Core / JSON / OGC Process Description / Job list.

A survey of the standard's repository (2026-08-31,
[opengeospatial/ogcapi-processes](https://github.com/opengeospatial/ogcapi-processes))
found the spec has grown to **five parts**, four of them unreleased drafts
under active daily editing:

| Part | Status | Substance |
|---|---|---|
| 1: Core v1.0 (18-062r2) | **approved IS** — the only one | list/describe/execute/jobs/results |
| 1: Core v2.0 (18-062r3) | editor's draft | absorbs `collection-input` / `collection-output` / `remote-collections` / `local-filtering` from Part 3; adds `kvp-execute`, `callback`, `dismiss` (job cancel), OpenAPI 3.1 |
| 2: Deploy, Replace, Undeploy (20-044) | draft | `POST/PUT/DELETE /processes` carrying an Application Package; conf classes `ogcapppkg`, `cwl`, `docker` |
| 3: Workflows & Chaining (21-009) | draft | no new endpoints — nested/remote process execution, CWL + OpenEO grammars, CQL2 input/output field modifiers |
| 4: Job Management (24-051) | draft | `POST /jobs` creates a *pending* job, `PATCH` updates, `POST /jobs/{id}/results` starts it |
| 5: Provenance (26-038) | draft (new 2026) | `GET /jobs/{jobID}/prov` returning W3C PROV (PROV-JSON) for a completed job |

Two survey findings matter beyond bookkeeping:

- **Part 1 v2.0's headline addition is collection-centric execution** — a
  process whose inputs and outputs are OGC API collections. That is this
  platform's native model (source collection → process → output
  collection), and it dissolves the weakest joint in the §11 mapping:
  v1.0's `/results` was designed for value/href outputs, and representing
  "N catalog items" there was always going to be a contortion. Our
  collection-mediated chaining (output items re-trigger downstream
  processes via `item_events`) is likewise the workflow style the drafts
  bless — a workflows story with no nested-execution machinery.
- **Part 5 wants data the run ledger already holds**: run → pinned
  revision, `input_items`, `output_items`, timings, triggering event. A
  PROV-JSON rendering per run is nearly free *as long as that linkage
  stays queryable*.

## Decision

**Conformance is pursued as a facade over the canonical internal model —
never by reshaping the model to the standard.** Processes keep group
ownership, immutable revisions, and event triggers; the facade translates.
Within that:

1. **Claim targets stay Part 1 v1.0** (Core, JSON, OGC Process
   Description, Job list) — the only approved IS. Draft parts are not
   claim targets until published.
2. **Design the facade against the v2.0 draft's shapes where it costs
   nothing**, and represent our output collections as v2.0
   `collection-output` when it publishes, rather than contorting
   `output_items` into v1.0 `/results` beyond a plain links document. The
   facade keeps a thin mapping layer, because draft conformance URIs are
   still moving (Part 3's collection classes migrated into Part 1 v2.0
   mid-2026).
3. **`dismiss` (job cancel) is not claimed** until a native cancel-run
   verb exists (ISSUES I-81). The facade never fakes a verb the platform
   does not have.
4. **Part 2 (DRU) is out of facade scope** — it would expose deploy
   externally, a separate auth/ownership decision. One constraint binds
   now: the future `container` runtime's contract (ADR 0013's deferred
   slice) must be *able to carry* an OGC Application Package (image
   reference + limits), so Part 2 is not foreclosed. The verb mapping is
   already compatible: `PUT` replace ≈ deploy-new-revision (insert +
   repoint, never mutate), `DELETE` ≈ soft-delete.
5. **Part 3 (nested/ad-hoc workflow execution) is documented as not
   planned, with reasoning**: platform runs are *triggered* by collection
   events, not composed per-request; chaining is expressed through
   collections, which is the equivalent capability in the style Part 1
   v2.0 standardizes. CWL/OpenEO grammars have no platform counterpart.
6. **Part 4 (pending-job lifecycle) is watch-only.** It would require
   client-created pending rows in `process_runs`; no current driver
   justifies that ledger change. The ADR 0004 request-table bridge is the
   internal analog if one ever appears.
7. **Part 5 (provenance) is a planned cheap add-on to the facade**, and it
   creates one schema-evolution invariant **effective now**: the
   run ↔ source-item ↔ output-item linkage (`process_runs.input_items` /
   `output_items` / revision pin) stays queryable — no cleanup or
   retention change may break the ability to reconstruct a completed
   run's provenance while the run row lives.

## Consequences

- The facade remains an unscheduled post-M3 stretch; nothing here changes
  sequencing (M3 next — ROADMAP §9 steering order).
- Two constraints bind current work: the container-runtime contract must
  not foreclose Application Packages (4), and provenance linkage stays
  queryable (7).
- The standard is re-surveyed when the facade slice is green-lit; this
  ADR's parts table is a snapshot, not a pin. If by then a part still
  cannot be claimed without model damage, the facade documents the gap
  (per the lead's directive) rather than forcing it.

## Revisit

- When Part 1 v2.0 or Part 2 publishes as an approved IS.
- When the `container` runtime slice is scoped (check constraint 4).
- When a cancel-run verb lands (claim `dismiss`, close I-81's OGC half).
