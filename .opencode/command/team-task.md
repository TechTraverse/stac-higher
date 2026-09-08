---
description: Run the multi-agent lead loop on one TODO.md queue, dispatching parallel work to teammate subagents. Usage: /team-task G
---

You are the **lead** for this repo's development loop. Teammates implement;
you classify, dispatch and integrate.

**Queue: `$ARGUMENTS`**

If that is empty, STOP and ask which queue. `TODO.md` holds seven independent
queues; you work only the one named.

## Setup

Same as `/solo-task`: `AGENTS.md` is binding, read the `project-conventions`
skill, grep `TODO.md` for your queue's section (not the whole file), read the
design spec it names, and confirm `ai/main` is green with `npm run verify`
before dispatching anything.

## Classify before dispatching

For each batch of queue items, pick a mode:

- **PARALLEL** — the tasks touch disjoint files (e.g. one in
  `app/src/components/search/`, one in `packages/shared/src/components/map/`,
  one in `services/pipeline/`). One teammate per task group.
- **SEQUENTIAL** — the tasks share files, build on each other, or all sit in one
  subsystem. Do it yourself with `/solo-task`'s loop; warm context beats
  parallelism.

**When unsure, choose SEQUENTIAL.** Merge conflicts cost more than serial time.

## PARALLEL mode

Dispatch with the `task` tool, `subagent_type: teammate` — that agent already
carries the verify-only rule and has Docker, the dev server, e2e, `git merge`
and `git push` **denied at the permission layer**, so the singleton rule is
enforced rather than merely requested.

Each teammate prompt must be self-contained:

- Its worktree path and branch name (`.claude/worktrees/<slug>`, `ai/<slug>`),
  created off `ai/main`
- Exactly which queue items / spec sections to read and implement
- Which files are in scope, and that anything else is out of scope
- What to report back

Dispatch all teammates in one message so they run concurrently. Do not
duplicate their work while they run.

## Integration — yours alone

After every teammate has reported:

1. `git checkout ai/main`
2. Merge each branch in order: `git merge ai/<slug> --no-ff`
3. Conflicts: resolve per AGENTS.md — read both sides, understand intent,
   produce a correct merge. `package-lock.json` →
   `git checkout --theirs package-lock.json && npm install && git add package-lock.json`;
   never hand-edit the lockfile. STOP only if the sides genuinely contradict.
4. `npm run verify` on `ai/main`. Merge-induced breakage (missing import, type
   error) — fix and commit on `ai/main`. Anything deeper — STOP.
5. If the batch changed UI flows the e2e suite covers: bring up the Docker
   backend, then run `npm run test:e2e:ci` from `app/` **once, serially**. Read
   the `run-e2e` skill first.
6. Remove the worktrees, delete the merged branches.
7. Mark the items `- [x]` in `TODO.md`, append follow-ups, and report
   "<batch> complete. ai/main is green."

## Reading large files

`TODO.md`, `ROADMAP.md`, `docs/FEATURES.md`, `docs/ISSUES.md` are 600–1400
lines each. Grep for the task ID or heading and read that window — never the
whole file. Pass teammates the specific section to read, not the file.

## Never

- Never commit to `main`. Promotion is a human-reviewed PR `ai/main → main`.
- Never push `ai/main` without asking the human first.
- Never let a teammate merge, run e2e, run the dev server, or touch Docker.
- Never run two e2e suites or two dev servers concurrently.
- Never give one teammate two unrelated tasks.

## Stop conditions

HALT and report — the task that failed, the exact error or conflict, which
branches exist and their state, a suggested next step — if:

- A merge conflict is ambiguous
- `npm run verify` fails after a merge and the fix is not obvious
- A teammate reports ambiguous requirements or a broken prerequisite
- A teammate fails to complete its task
- e2e or the Docker smoke test fails for a cause outside this batch
