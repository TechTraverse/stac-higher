---
description: Run the autonomous solo loop on one TODO.md queue (M3, G, P, X, K, W, V). Usage: /solo-task M3
---

You are running this repo's solo implementation loop.

**Queue: `$ARGUMENTS`**

If that is empty, STOP and ask which queue. `TODO.md` holds seven independent
queues and the first unchecked item in the FILE is almost never the right one.
Guessing costs a worktree and a merge.

## Setup

1. Read `AGENTS.md` — binding. Read the `project-conventions` skill.
2. Grep `TODO.md` for the `## $ARGUMENTS queue` heading and read that section
   only. It opens with a **Read first** line naming its design spec under
   `docs/superpowers/specs/` and whether that spec is approved.
3. Read the spec section the task cites. Do not relitigate anything the spec
   marks settled.
4. Confirm `ai/main` is green: `git checkout ai/main`, then `npm run verify`.
   If it fails, STOP and report.

## The loop — one task per iteration

1. Take the **first unchecked (`- [ ]`) item in your queue**. No cherry-picking
   within the queue; no wandering into another queue.
2. Worktree off `ai/main` (never `origin/main`):
   `git worktree add .claude/worktrees/<slug> -b ai/<slug> ai/main`, then
   `npm install` in it.
3. Read the files the task references. Reuse existing components — `StacMap`,
   `FootprintLayer`, `ExtentLayer`, `BboxInput`, and the rest of
   `@stac-higher/shared`. Keep the change minimal and focused.
4. `npm run verify` must pass. If the task touched `services/pipeline/`, also
   `uv run pytest` and `uv run ruff check .` from `services/pipeline/`.
5. Run e2e only if the task touched flows the suite covers — read the `run-e2e`
   skill first, and run it serially, never alongside a dev server.
6. Merge `--no-ff` into `ai/main`, remove the worktree, delete the branch.
7. Mark the item `- [x]` in `TODO.md` and append discovered follow-ups to that
   queue's follow-ups section.

Then start the next iteration, or stop if the queue is done.

## Reading large files

`TODO.md` (914 lines), `ROADMAP.md` (1353), `docs/FEATURES.md` (637) and
`docs/ISSUES.md` (1129) must never be read whole — that is tens of thousands of
tokens to find one entry. Grep for the task ID (`M3-A`, `G-6`, `I-100`,
`ADR 0014`) or the section heading, then read that window.

## Never

- Never commit to `main`. Promotion is a human-reviewed PR `ai/main → main`.
- Never push `ai/main` without asking the human first.
- Never run two dev servers or two e2e suites at once.
- Never hand-edit `components/ui/` (the plugin guard will refuse it anyway).
- Never combine unrelated tasks in one commit.

## Stop conditions

HALT and report — with the failing task, the exact error, the branches that
exist and their state, and a suggested next step — if:

- A merge conflict is genuinely ambiguous (the two sides contradict)
- `npm run verify` fails after a merge and the fix is not obvious
- The task's requirements are ambiguous or a prerequisite is broken
- e2e fails for a cause outside this task's changes
