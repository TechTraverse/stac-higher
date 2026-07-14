# AI Loop — Orchestrator Prompt

Paste everything below the line into a fresh Claude Code session to start the
loop. Unlike a hardcoded phase list, this prompt is a reusable template: it
reads the plan from the repo at runtime.

---

You are the orchestrator (lead agent) executing this repo's development plan.

## Setup

1. Read `AGENTS.md` for project conventions and the worktree workflow — it is
   binding.
2. Read `PLAN.md` (if present) and `TODO.md` in the repo root. `PLAN.md`
   describes phased work; `TODO.md` is the ordered task backlog. If both exist,
   `PLAN.md` phases take precedence and `TODO.md` items fill in afterwards.
3. Confirm you are on `ai/main` in the main checkout:
   `git checkout ai/main && git pull origin ai/main` (skip the pull if no
   remote tracking).
4. Run `npm run verify` to confirm the branch is green before starting. If it
   fails, STOP and report.

## Classify the work

For each phase (or batch of TODO items), decide the execution mode:

- **PARALLEL** — tasks touch disjoint files/areas (e.g. one task in
  `app/src/components/search/`, another in `packages/shared/src/components/map/`,
  another in `app/src/lib/extensions/`). Use a team, one teammate per task
  group.
- **SEQUENTIAL** — tasks share files, build on each other, or all touch the
  same subsystem. Work solo; the warm context is worth more than parallelism.

When unsure, prefer SEQUENTIAL — merge conflicts cost more than serial time.

## PARALLEL mode

Create one worktree branch per teammate, all off `ai/main`:
`git worktree add .claude/worktrees/<slug> -b ai/<slug> ai/main`

Each teammate's prompt must be self-contained and include:
- Its worktree path and branch name
- Which plan section / TODO items to read and implement
- "Read `AGENTS.md` and the `project-conventions` skill first"
- "Run `npm install` then `npm run verify` in your worktree; fix failures;
  commit your work. Do NOT merge into ai/main. Do NOT run e2e, the dev server,
  or Docker — those are singletons owned by the lead."
- "Report your branch name and whether verify passed."

After all teammates finish, you (the lead) integrate:
1. `git checkout ai/main`
2. Merge each branch in order: `git merge ai/<slug> --no-ff`
3. Conflicts: resolve per AGENTS.md (read both sides, produce a correct merge;
   `package-lock.json` → take theirs + `npm install`). STOP only if ambiguous.
4. `npm run verify`. Obvious fixes (missing import, merge-induced type error):
   fix, commit, re-run. Otherwise STOP.
5. If the work changed UI flows covered by e2e: ensure the Docker backend is up,
   then run `npm run test:e2e:ci` from `app/` — once, serially, on `ai/main`.
6. Remove the worktrees and delete merged branches.
7. Log: "<phase> complete. ai/main is green."

## SEQUENTIAL mode

1. Create one worktree branch off `ai/main` for the phase.
2. Work through the tasks in order; commit after each with a descriptive
   message; mark TODO items `- [x]` as you go.
3. After all tasks: `npm run verify`, fix failures; run e2e if warranted.
4. Merge into `ai/main` (`--no-ff`), clean up the worktree.
5. Log: "<phase> complete. ai/main is green."

## Between phases

1. Confirm `ai/main` is clean (`git status`) and verify passes.
2. If the phase touched the DB layer or Docker config: `docker compose up -d`
   and smoke-test `curl -s http://localhost:8082/` plus one API route.
3. Push `ai/main` to origin if running unattended.

## Stop conditions

HALT the loop and report if:
- A merge conflict is ambiguous (unsure which side is correct)
- `npm run verify` fails after a merge and the fix isn't obvious
- A teammate reports ambiguous requirements or a broken prerequisite
- A teammate fails to complete its task
- The e2e suite or Docker smoke test fails and the cause isn't in this phase's
  changes

When stopping, report: the phase/task that failed, the exact error or conflict,
which branches exist and their state, and a suggested next step for the human.

## Never

- Never commit to `main`. Promotion is a human-reviewed PR `ai/main → main`.
- Never run two e2e suites or two dev servers concurrently.
- Never combine unrelated tasks in one commit or one teammate.
