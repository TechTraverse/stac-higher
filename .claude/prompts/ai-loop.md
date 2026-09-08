# AI Loop — Orchestrator Prompt

Paste everything below the line into a fresh Claude Code session to start the
loop. It is a reusable template: it reads the plan from the repo at runtime.
One session, one or more **lanes**; a lane is one `TODO.md` queue worked
top-down. Two sessions must never work the same repo at once (the parallel-
session hazard of 2026-09-04: two controllers drove one worktree and one merged
early).

---

You are the orchestrator (lead agent) executing this repo's development plan.

**Lanes: `<LANES>`** ← the human fills this in, e.g. `M3, V` (or a single
queue such as `K`). `TODO.md` holds several independent queues; you work ONLY
the queues named here, each as its own lane, and nothing else. If it is blank,
stop and ask before doing anything else.

**Docker policy: `<DOCKER>`** ← `full` (default when the M3 lane is present),
`smoke` or `none`. See "Docker" below.

## Setup

1. Read `AGENTS.md` (via `CLAUDE.md`) — binding. Read the
   `project-conventions` skill.
2. Read the `TODO.md` header, then each lane's queue section. A queue opens
   with a **Read first** line naming its spec under `docs/superpowers/specs/`
   and says whether it is approved. Read the spec. Take the first unchecked
   item in EACH lane's queue (never the first in the file), and read the
   spec sections and the **task plan** it cites. Plans live in
   `docs/superpowers/plans/` and are named by slice
   (`2026-09-07-m3-a-pgstac-write-path.md`, `…-m3-b-connection-pool.md`,
   `…-v2-map-page-shell.md`, `…-v3-map-imagery-time-axis.md`). A slice
   without a plan gets one BEFORE any code: invoke `superpowers:writing-plans`
   against the spec section, the slice text, and the queue's "Carried
   forward" block, save it beside the others, and only then implement.
3. Peer check (mandatory, every start and every restart notice):
   `ListAgents`, `git worktree list`, `git branch --list 'ai/*'`,
   `git log ai/main -5`. A live peer session, an unfamiliar worktree, or
   commits on `ai/main` you did not make mean another controller is active —
   coordinate or stand down; never double-drive.
4. `git checkout ai/main` in the main checkout (no pull — `ai/main` has no
   remote by the lead's standing instruction). Run `npm run verify` and, if a
   lane touches the pipeline, `cd services/pipeline && uv run pytest && uv run
   ruff check .`. If either fails, STOP and report.

## Model policy

Subagents are **Sonnet for implementation** and **Opus for review and
plan-writing**. Never dispatch a Fable subagent (the lead's standing
instruction, 2026-09-04). The lead itself runs whatever model the session was
started with.

## Lanes

Lanes are independent by construction (`TODO.md` header: slices in different
queues never block each other; a shared file is resolved by the later merge).
Run them **concurrently, one slice per lane at a time**:

- Each slice runs through `superpowers:subagent-driven-development` in its own
  worktree off `ai/main`:
  `git worktree add .claude/worktrees/<slug> -b ai/<slug> ai/main` (the plan's
  Global Constraints name the slug), then `npm install` there for app slices.
  Teammates get the plan's task text, the standing constraint "run `npm run
  verify` (and pytest + ruff for pipeline tasks) only — no e2e, no dev server,
  no Docker", and report their branch and whether the gate passed.
- The lead is the only process that touches the singletons: the dev server
  (:4321), the Docker stack, the e2e suite, the load harness.
- When a lane's slice is complete and reviewed: re-run the peer check's
  `git log ai/main -5`, then `git checkout ai/main && git merge ai/<slug>
  --no-ff`, resolve conflicts per `AGENTS.md`, run `npm run verify` (+ pytest
  + ruff if the pipeline changed), then the slice's lead-only steps (the
  plan's final task: e2e for UI flows, Docker measurement, live check), then
  `git worktree remove` + `git branch -d`. Merge lanes in the order they
  finish; never hold one lane's merge for the other.
- After the merge: tick the slice in `TODO.md`, update the queue-table state,
  append the plan's "landed" note to the follow-ups block, and start the
  lane's next slice (plan first if it has none).
- A lane stops at a slice marked **LEAD ONLY** that needs the human (a cloud
  account, a promotion PR) — report and let the other lane continue.

## Docker

`full` — the lead may `docker compose build pipeline && docker compose up -d
pipeline` to deploy a merged pipeline slice, run **labelled** load-harness
probes (`pipeline.loadgen --label <slice-id> …`, always followed by
`teardown`), and use the standing GOES demo (`goes-abi-mcmipc` →
`goes-geocolor`, seeded 2026-09-04) as the live canary that a slice works on
real data. Probes slow the demo's ingest while they run; that is accepted.
`smoke` — rebuild + restart + canary only, no probes; every measurement is
left for the human and recorded as owed in `TODO.md`. `none` — teammate
rules for the lead too; a slice whose own text requires a live check cannot
be declared done and is left ticked-pending with the owed step listed.

Never, under any policy: `docker compose down -v`; delete or disable the
standing demo's connection, association or collections; run a probe without
a label; leave a probe's rows behind (teardown is re-runnable — run it).

## Preamble (lead, once, `full` or `smoke` only)

Before the first slice, with the stack up (`docker compose up -d --wait`,
`set -a; source .env; set +a`), close the cheap live checks the last sessions
left owed, read-only against the standing demo:

- **D-1/D-2 live check.** `GET :8081/collections/goes-geocolor/items?limit=1`
  → the item carries a `rel: "derived_from"` link (D-1 stamps it at finalize;
  the belief to verify is that pgstac keeps the rel through the upsert). Then
  open that item on `/collections/goes-geocolor/items/<id>` in Chrome, click
  the "Derived from" chip, land on the `goes-abi-mcmipc` source item. Record
  the result under D-1's and D-2's TODO entries (both say "live check owed").
  If pgstac strips the link, log it in `docs/ISSUES.md` and `TODO.md` and do
  NOT start fixing it — report.

Everything else lead-only (X-5's gates, G-8's night frame) stays parked
unless named in `<LANES>`.

## Stop conditions

HALT a lane (and say which) if: a merge conflict is genuinely ambiguous;
`npm run verify` / pytest fails after a merge and the fix is not obvious; a
teammate reports a broken prerequisite or an unclear requirement the plan and
spec do not settle; a plan's live-check step fails for a reason outside the
slice's changes. The other lane continues. HALT the session if the peer
check finds another controller, or the Docker stack is unhealthy in a way
the slice did not cause.

When stopping, report: the lane and task, the exact error or conflict, every
`ai/*` branch and worktree and its state, and the suggested next step.

## Never

- Never commit to `main`; never push `ai/main` (promotion is a human PR).
- Never run two e2e suites, two dev servers or two probes concurrently.
- Never combine unrelated tasks in one commit or one teammate.
- Never cherry-pick within a queue or wander across queues.
