---
description: Run the multi-agent lead loop on one GitHub Issues queue, dispatching parallel work to teammate subagents. Usage: /team-task K
---

You are the **lead** for this repo's development loop. Teammates implement;
you classify, dispatch and integrate.

**Queue: `$ARGUMENTS`**

If that is empty, STOP and ask which queue. The backlog is GitHub Issues, one
`queue: <name> (<code>)` label per queue (resolve the code with `gh label
list --search "(<code>)"`); you work only the one named.

## Setup

Same as `/solo-task`: `AGENTS.md` is binding, read the `project-conventions`
skill, list the queue's open issues, read its `epic` issue and the design spec
it names, and confirm `main` is green (`git fetch origin && git checkout main
&& git pull --ff-only`, `npm run verify`) before dispatching anything.

## Classify before dispatching

For the `ready`, unassigned issues in the queue, pick a mode:

- **PARALLEL** — the issues touch disjoint files (e.g. one in
  `app/src/components/search/`, one in `packages/shared/src/components/map/`,
  one in `services/pipeline/`). One teammate per issue.
- **SEQUENTIAL** — the issues share files, build on each other, or all sit in
  one subsystem. Do it yourself with `/solo-task`'s loop; warm context beats
  parallelism.

**When unsure, choose SEQUENTIAL.** Rebase conflicts cost more than serial time.

## PARALLEL mode

Assign each issue to yourself first (the claim), then dispatch with the `task`
tool, `subagent_type: teammate` — that agent already carries the verify-only
rule and has Docker, the dev server, e2e, `git merge` and `git push`
**denied at the permission layer**, so the singleton rule is enforced rather
than merely requested.

Each teammate prompt must be self-contained:

- Its issue number, worktree path and branch name
  (`.claude/worktrees/<slug>`, `feat/<slug>`), created off `main`
- Exactly which spec sections and plan to read and implement
- Which files are in scope, and that anything else is out of scope
- What to report back

Dispatch all teammates in one message so they run concurrently. Do not
duplicate their work while they run.

## Integration — yours alone

After every teammate has reported, for each branch in the order they finished:

1. In the branch's worktree: `git fetch origin && git rebase origin/main`.
   Conflicts: resolve per AGENTS.md — read both sides, understand intent,
   produce a correct result. `package-lock.json` →
   `git checkout --theirs package-lock.json && npm install && git add package-lock.json`;
   never hand-edit the lockfile. STOP only if the sides genuinely contradict.
2. `npm run verify` in the worktree (plus pytest + ruff if the pipeline
   changed). Rebase-induced breakage (missing import, type error) — fix and
   commit on the branch. Anything deeper — STOP.
3. If the issue changed UI flows the e2e suite covers: bring up the Docker
   backend, then run `npm run test:e2e:ci` from `app/` **once, serially**. Read
   the `run-e2e` skill first.
4. `git push -u origin feat/<slug>`; `gh pr create --base main --title "<ID>:
   <title>"` with a body that starts `Closes #<n>` and lists the gates run,
   the lead-only steps left and deviations from the plan. Wait for CI, then
   `gh pr merge --squash --delete-branch`.
5. Remove the worktree. Open issues for follow-ups (Slice template). Report "<batch> complete. main is green."

## Reading large files

`ROADMAP.md`, `docs/FEATURES.md`, `docs/ISSUES.md` are 600–1400 lines each.
Grep for the task ID or heading and read that window — never the whole file.
Pass teammates the specific section to read, not the file.

## Never

- Never commit to or push `main`; every change is a PR.
- Never let a teammate merge, push, run e2e, run the dev server, or touch Docker.
- Never run two e2e suites or two dev servers concurrently.
- Never give one teammate two unrelated issues.
- Never take a `blocked` issue or one assigned to someone else.

## Stop conditions

HALT and report — the issue that failed, the exact error or conflict, which
branches and PRs exist and their state, a suggested next step; leave the same
summary as a comment on the issue — if:

- A rebase conflict is ambiguous
- `npm run verify` fails after a rebase and the fix is not obvious
- A teammate reports ambiguous requirements or a broken prerequisite
- A teammate fails to complete its task
- e2e or the Docker smoke test fails for a cause outside this batch
