# TODO — Phase 7 work queue (push ingest)

The solo agent loop (AGENTS.md) works this file top-down: pick the **first
unchecked item**, one task per iteration, worktree off `ai/main`, `npm run
verify` (+ pipeline `pytest`/`ruff` when the pipeline is touched) before merge.

Scope sources: **ROADMAP §9 Phase 7** (done-when), **§6.2** (push-ingest flow),
**§6.4** ("Finalize gating"), **ADR 0014** ("Finalize seam sketch" — the
producer-parameterized `FinalizeRequest` obligation Phase 7's finalize MUST
satisfy, with the P9-B review criterion), and the Phase 9 scoping notes
(`docs/superpowers/specs/2026-08-29-phase9-scoping-notes.md`). Migrations are
at 019 — Phase 7 takes 020+; M5 renumbers from wherever Phase 7 ends.

**Phase 7 gate (done-when):** an external client with a token can upload a
file and POST an item; the item finalizes into canonical storage (validate →
checksum → move staging→canonical → rewrite hrefs); and delivery fires from it
like any other item — with the dispatcher deferring staged items until
finalize marks them ready (§6.4), so delivery never streams from staging and
never double-fires on the href rewrite.

The M2 queue is closed — it lives in git history at `650d76de` (evidence:
ROADMAP §9 M2).

**Standing decisions for this run (lead away, recorded 2026-08-29):**
- **Spec gate:** the Phase 7 design spec is drafted, adversarially reviewed by
  a subagent, then recorded as **provisionally approved** — implementation
  proceeds without waiting for the human. The human reviews spec + code on
  return; rejected pieces get reworked.
- **Docker/e2e allowed:** the lead may run the compose stack, rebuild the
  pipeline image (grep build output for `ERROR` — the BuildKit exit-0 gotcha),
  and run e2e serially after merges. Use `E2E_PORT=4399` (Cursor may own
  :4321). Teammates still run `npm run verify` only.
- **Never push `ai/main` to origin** (user preference — overrides the
  AGENTS.md/ai-loop unattended-push line).
- Promotion `ai/main → main` remains the human's PR.

## Independent bug workstream (parallel-safe — disjoint from Phase 7 files)

- [x] **P7-X · I-67 item edit form fix** (ISSUES **I-67** 🔴, + the Astro-7
      follow-up). `/collections/[id]/items/[itemId]/edit` crashes with "Could
      not find a definition for https://geojson.org/schema/Geometry.json" —
      RJSF cannot resolve the remote GeoJSON `$ref` that rides in with the
      projection extension (`proj:geometry`), so the edit form is broken for
      EVERY pipeline-ingested item. Fix in the RJSF schema-resolution layer
      (the extension schema path already has a resolve-and-cache seam:
      `/api/extensions/resolve-schema`) or by pre-resolving/stripping remote
      `$ref`s before handing the schema to RJSF — read the ISSUES entry for
      the options already considered. **Fix together** (same neighborhood):
      the nested-`<form>` React hydration warning on ItemFormPage (RJSF
      extension-fields form inside the RHF form — render RJSF without its own
      `<form>` tag / use its `tagName` option). Unit-test the resolution path
      with a fixture item carrying `proj:geometry`; e2e is the lead's if the
      flow is covered. Update ISSUES I-67 on close.
      **Done 2026-08-29** (`ai/p7x-i67`, merged) — schema-preparation layer
      `lib/extensions/ref-resolve.ts` (`prepareExtensionSchema`): hoists the
      STAC extension envelope's `definitions.fields` to the form root,
      resolves `proj:geometry` against BUNDLED geojson.org schemas (offline,
      zero fetches for the common case), inlines other remote refs through
      the existing `/api/extensions/resolve-schema` seam (cache now serves
      stale on upstream failure), and degrades any unresolvable ref to a
      contained raw-JSON field editor — no remote ref ever reaches RJSF.
      Nested-form warning: `tagName="div"` on the repo's single RJSF
      instantiation (fixes ItemForm AND CollectionForm); submit stays with
      the RHF form, merge-into-properties asserted by test. 24 new tests.
      I-67 flipped 🟢. Follow-ups below.

## Phase 7 — design spec first

- [ ] **P7-A · Phase 7 design spec** (the M2/M5 pattern —
      `docs/superpowers/specs/2026-08-29-phase7-push-ingest-design.md`).
      Consume ROADMAP §9 Phase 7 + §6.2 + §6.4 finalize gating + ADR 0014's
      seam sketch. Must settle, at minimum:
      - **Proxy write policy**: how `collection_settings.externally_writable`
        (exists since migration 003, never enforced) gates transaction writes
        at stac-auth-proxy for external clients while the BFF path (ADR 0008)
        keeps working; auth-enforced compose overlay changes.
      - **Staged-upload contract**: external clients reuse `POST /api/uploads`
        (presigned PUTs into `staging/`) — confirm scoping/auth for
        non-browser clients, and how a POSTed item references staged assets.
      - **Finalize**: a pipeline job implementing the ADR 0014
        producer-parameterized seam (`FinalizeRequest{producer,
        staging_prefix, output_collections, items, provenance}` — no producer
        branching inside validate/checksum/move/rewrite/upsert; push-ingest is
        merely the first caller). Validation library choice (stac-pydantic vs
        stac-validator), failure semantics (reject → where does the client
        see it?), idempotency/crash-safety (mark-first patterns per ADR 0011
        where bytes move).
      - **Dispatcher deferral** (§6.4): staged items held until finalize marks
        ready; no double-fire on the href rewrite.
      - **Cross-runtime contracts**: enumerate new shapes → golden fixtures in
        `tests/contract-fixtures/` (both suites consume).
      - **Migrations**: 020+ (app owns DDL — ADR 0001); alerting hooks
        (finalize failures → the M2-B alert model, inside `MONITOR_KINDS`
        ownership rules); `/metrics` counters per the M2-H central pattern.
      - **API client docs** scope (how to authenticate, upload, POST items —
        a `docs/push-ingest.md` or similar).
      - **Slicing** into P7-B… implementation tasks.
      Then: adversarial review by a fresh subagent (spec vs. ROADMAP/ADRs/
      ISSUES — the P9-F pattern), fix findings, record **provisional
      approval** per the standing decision, add an ADR if any choice is
      significant + hard-to-reverse, and **seed the implementation queue
      below** (replacing the placeholder) with worktree-sized tasks whose
      file footprints are marked parallel-safe or sequential.

## Phase 7 — implementation (seeded by P7-A)

- [ ] **P7-B… · seeded by P7-A** — replace this placeholder with the sliced
      implementation queue when the spec is provisionally approved.

## Phase 7 gate

- [ ] **P7-Z · live gate check** (lead only: Docker + dev server + e2e). On
      the auth-enforced stack: mint a real token, upload via presigned PUT,
      POST an item as an external client → finalize moves bytes to canonical
      + rewrites hrefs → a delivery association fires from it; a finalize
      failure alerts. Run the full e2e suite (`E2E_PORT=4399`). Record
      evidence in ROADMAP §9 (M-gate style), update FEATURES.md / ISSUES.md /
      ROADMAP phase table, and STOP the loop with a summary for the human
      (promotion PR is theirs).

## Discovered follow-ups

(append here during iterations)

### From P7-X (I-67 fix)

- The item edit flow may be covered by e2e — the lead's next e2e run (P7-Z at
  the latest) doubles as the live regression check for this fix.
- `proj:geometry` now renders as the full GeoJSON oneOf (nested coordinate
  array editors) — functional but heavy. A map-backed or raw-JSON uiSchema
  default for geometry-typed extension fields would be nicer UX.
- Fully-offline FIRST render of an extension still needs one successful fetch
  of the extension schema itself ever (the stale-cache fallback covers it
  afterward); bundling common STAC extension schemas would close that.
- `proj:projjson` always degrades to the raw-JSON editor (its schema's
  internal refs can't be rebased) — deliberate, arguably the right editor for
  PROJJSON; noting so nobody "fixes" it blind.
- Shell gotcha: `npm run verify 2>&1 | tail` masks the exit code (the pipe
  returns tail's status) — same class as the BuildKit exit-0 note.
