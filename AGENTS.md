# AGENTS.md

Canonical instructions for all AI coding agents in this repo, per the
[AGENTS.md](https://agents.md/) standard. Harness-specific additions live in
that harness's own file (`CLAUDE.md`, `opencode.json`). This file is loaded
into every session: facts and rules only. Procedures and area-specific rules
live in the skills and `docs/` — read them when the task touches that area.

## Layout

npm-workspaces monorepo: `app/` (Astro 7 SSR + React 19 STAC client),
`packages/shared/` (`@stac-higher/shared`: components, hooks, stores, map,
RJSF theme, Storybook), `services/pipeline/` (Python queue worker +
scheduler), `services/process-runtime/` (process-run image), `infra/` +
`docker-compose.yml` (local stack), `tests/contract-fixtures/` (cross-runtime
golden fixtures).

- `npm install` at the **repo root** only — single lockfile, workspace symlinks.
- **Shared components are the source of truth**: UI primitives, `shared/*`
  utilities, cards, all `map/` components and the RJSF theme live in
  `packages/shared/` and are imported from `@stac-higher/shared`; most
  `app/src/lib/` and `app/src/stores/` files are thin re-export proxies.
- Path aliases: `@/*` → `app/src/*`; `@shared/*` → `packages/shared/src/*`
  (inside the shared package only).

## Commands

- **Install**: `npm install` (repo root)
- **Verify**: `npm run verify` (repo root — app-scoped typecheck + build +
  unit tests, the CI gates; **must pass before declaring any task done**).
  Never run `npx astro check` from the repo root — it has no `src/pages`
  there and reports nothing useful.
- **Dev**: `npm run dev` (from `app/`, http://localhost:4321). Astro 7
  daemonizes `astro dev` under AI agents; manage with `astro dev stop|status|logs`.
- **Unit tests**: `npm test` / `npm run test:watch` (from `app/`)
- **Pipeline tests**: `uv run pytest` and `uv run ruff check .` (from
  `services/pipeline/` — both, whenever the pipeline is touched)
- **E2E**: `npm run test:e2e:ci` (from `app/`). Lead-only; read the `run-e2e`
  skill first.
- **Backend**: `docker compose up -d` (repo root). Services, ports,
  credentials and the auth-enforced overlay: `docs/backend.md`.
- **Demo pipeline**: `uv run python -m pipeline.demo seed | status | teardown`
  (from `services/pipeline`, stack up). **Storybook**: `npm run storybook`
  (from `packages/shared/`).

## Architecture in one paragraph

Astro pages are thin shells that each mount one React island; state is
three-tier (nanostores for cross-island persistent state, TanStack Query for
server state, React Hook Form + Zod for forms); there is **no global "active
catalog"**; maps are MapLibre via `react-map-gl/maplibre` with shared
`StacMap` / `FootprintLayer` / `ExtentLayer` components. The rules and the
why: the `project-conventions` skill — read it before any non-trivial change.
Cross-cutting backend rules (schema ownership, contract fixtures, RBAC +
audit, egress, process isolation, GC): the `backend-invariants` skill — read
it before touching the pipeline, API routes, migrations or images.

## Workflow — trunk-based, one PR per issue (Mandatory)

`main` is the only long-lived branch. Every change — human or agent — lands
through a short-lived branch and a squash-merged pull request into `main`.
**Never commit directly to `main`.** (`ai/main` is retired; anything that
still names it is historical.)

1. **Pick an issue**: a GitHub issue labelled `ready` in the queue you were
   asked to work (`gh issue list --label "queue: k8s compute (K)" --label ready`;
   the pinned "Start here" issue maps codes to names). Assign yourself — that
   is the claim. Not `blocked`, not `lead-only` unless you are the lead.
2. **Start**: `git worktree add .claude/worktrees/<slug> -b feat/<slug> main`
   (`fix/`, `docs/` for those kinds of change), then `npm install` in the
   worktree. Never work in the main checkout.
3. **Work**: read the spec section and plan the issue cites before changing
   anything; a slice without a plan gets one first (`superpowers:writing-plans`,
   committed under `docs/superpowers/plans/`). One issue per branch; minimal,
   focused changes; reuse existing components. Run the gates (verify; plus
   pytest + ruff when the pipeline is touched) before declaring done.
4. **PR**: `git push -u origin feat/<slug>`, then `gh pr create --base main`
   with a body that starts `Closes #<n>` and lists the gates run, the
   lead-only steps left (e2e, Docker, live checks) and any deviation from
   the plan. Title `<ID>: <what changed>`. CI runs verify, the pipeline tests
   and the Storybook build.
5. **Merge**: squash-merge via the PR (the repo allows nothing else; branches
   auto-delete). `git worktree remove .claude/worktrees/<slug>`. Discovered
   follow-ups become new issues (Slice template, same queue label) or
   `docs/ISSUES.md` entries when they are limitations, not work. Nothing else
   is hand-updated: milestones and the epics' sub-issue bars are the only
   live status.

Rules:
- Base branch is always `main`; rebase a long-running branch before its PR.
- **Singleton resources**: the dev server (:4321), the pgstac backend (:8082),
  the Docker stack, the load harness and the e2e suite (serial, shared DB).
  Only ONE process may run them at a time — in team work, the lead.
  Teammates run `npm run verify` (and pytest + ruff) **only** and never push.
- **Merge conflicts**: read both sides, understand intent, produce a correct
  merge. STOP and report only if the sides genuinely contradict.
  `package-lock.json`: `git checkout --theirs package-lock.json && npm install
  && git add package-lock.json` — never hand-edit the lockfile.
- **Migration numbers** are reserved on the issue (`migration` label).
- If `main` is red after a merge, the fix is a `fix/` PR, not a push.
- No new dependencies without clear need. Never hand-edit shadcn
  `components/ui/` files (`npx shadcn@latest add <component>`).
- Team orchestration is harness-specific: Claude Code `CLAUDE.md` "Team
  tasks" + `.claude/prompts/ai-loop.md`; opencode `/team-task`.

## Backlog and docs — how they connect

GitHub Issues is the backlog. A queue is a milestone + an `epic` issue
(spec to read, settled decisions, slice order; sub-issues show progress) +
a `queue: <name> (<code>)` label. Labels: `ready`, `blocked` (body names the
blockers), `lead-only`, `migration`, `ci`; `agent:go` is reserved for the
Claude GitHub app. Human entry point: `CONTRIBUTING.md`.

Link rule: a document links its tracking issue once, in its header, and never
carries status — spec → `Tracking: epic #N`; issue → spec section + plan path;
PR → `Closes #N`; `docs/ISSUES.md` entry → `Tracked in: GitHub #N` only when it
is actionable; ADR → the adopting PR in its status line.

`docs/` (index: `docs/README.md`): **`FEATURES.md`** — what's built, per
phase, with entry points; **`decisions/`** — ADRs, add the next-numbered one
for any significant, hard-to-reverse choice; **`ISSUES.md`** — accepted
limitations, deferrals and residual risk (not a backlog); **`superpowers/`**
— dated specs and plans. Read the relevant track before non-trivial work
and update it after.

## Agent Skills

Task playbooks live in `.agents/skills/` ([Agent Skills](https://agentskills.io/)
standard; Claude Code reads them through the `.claude/skills` symlink). Read the
matching `SKILL.md` *before* starting: `project-conventions` (any non-trivial
change), `backend-invariants` (pipeline, API routes, migrations, images),
`new-component`, `new-page`, `new-test`, `add-stac-endpoint`, `run-e2e`. How
the pieces fit: `docs/AI-STRATEGY.md`.
