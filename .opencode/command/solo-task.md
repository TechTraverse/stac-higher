---
description: Run the autonomous solo loop on one GitHub Issues queue (M3, K, X, …). Usage: /solo-task K
---

You are running this repo's solo implementation loop.

**Queue: `$ARGUMENTS`**

If that is empty, STOP and ask which queue. The backlog is GitHub Issues,
one `queue:<X>` label per queue, and guessing costs a worktree and a PR.

## Setup

1. Read `AGENTS.md` — binding. Read the `project-conventions` skill.
2. `gh issue list --label "queue:$ARGUMENTS" --state open --json
   number,title,labels,assignees`. Read the queue's `epic` issue: it names the
   design spec under `docs/superpowers/specs/`, the settled decisions, the
   gate and the slice order.
3. Take the first `ready`, unassigned, non-`lead-only` issue in the epic's
   order and assign it to yourself (`gh issue edit <n> --add-assignee @me`).
   Read the spec section and the plan it cites. Do not relitigate anything
   the spec or epic marks settled.
4. Confirm `main` is green: `git fetch origin && git checkout main && git
   pull --ff-only`, then `npm run verify`. If it fails, STOP and report.

## The loop — one issue per iteration

1. Worktree off `main`:
   `git worktree add .claude/worktrees/<slug> -b feat/<slug> main`, then
   `npm install` in it. A slice without a plan gets one first
   (`superpowers:writing-plans`, committed on the branch under
   `docs/superpowers/plans/`).
2. Read the files the issue references. Reuse existing components — `StacMap`,
   `FootprintLayer`, `ExtentLayer`, `BboxInput`, and the rest of
   `@stac-higher/shared`. Keep the change minimal and focused.
3. `npm run verify` must pass. If the task touched `services/pipeline/`, also
   `uv run pytest` and `uv run ruff check .` from `services/pipeline/`.
4. Run e2e only if the task touched flows the suite covers — read the `run-e2e`
   skill first, and run it serially, never alongside a dev server.
5. Rebase onto a fresh `main`, re-run the gates, push, and open the PR:
   `gh pr create --base main --title "<ID>: <title>" --body "Closes #<n> …"`
   — the body lists the gates run, the lead-only steps left and any deviation
   from the plan. Wait for CI, then `gh pr merge --squash --delete-branch`.
6. Remove the worktree. Update the epic's slice list; open new issues for
   discovered follow-ups (same queue label, `lead-only` for live checks you
   could not run).

Then start the next iteration, or stop if the queue has no `ready` issue.

## Reading large files

`ROADMAP.md` (1300+ lines), `docs/FEATURES.md` and `docs/ISSUES.md` must never
be read whole — that is tens of thousands of tokens to find one entry. Grep for
the task ID (`M3-A`, `G-6`, `I-100`, `ADR 0014`) or the section heading, then
read that window.

## Never

- Never commit to or push `main`; every change is a PR.
- Never take a `blocked` issue, a `lead-only` issue, or one assigned to
  someone else.
- Never run two dev servers or two e2e suites at once.
- Never hand-edit `components/ui/` (the plugin guard will refuse it anyway).
- Never combine unrelated issues in one branch or one PR.

## Stop conditions

HALT and report — with the issue, the exact error, the branches and PRs that
exist and their state, and a suggested next step; leave the same summary as a
comment on the issue — if:

- A rebase conflict is genuinely ambiguous (the two sides contradict)
- `npm run verify` fails after a rebase and the fix is not obvious
- The issue's requirements are ambiguous or a prerequisite is broken
- e2e fails for a cause outside this task's changes
