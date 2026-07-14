# AI Workflow Restructure — Design

**Date**: 2026-07-13
**Status**: Approved (interactive session)

## Goal

Replace the single-branch (`ralph/progress`) monolithic-CLAUDE.md agent setup
with the worktree-isolated, standards-based layout used in
`~/Projects/TechTraverse/map-traverse`, adapted to this repo's constraints.

## Decisions

1. **Structure**: full AGENTS.md + Agent Skills adoption. `AGENTS.md` is
   canonical; `CLAUDE.md` becomes an `@AGENTS.md` shim holding only Claude-only
   content (EnterWorktree gotcha, hooks docs, team choreography). Long
   procedures move to `.agents/skills/` with a `.claude/skills` symlink.
2. **Branch model**: AI work merges to `ai/main` (created from `main`);
   `ralph/progress` deleted (it pointed at the same commit as `main`, no loss).
   Every task runs in a worktree branch `ai/<slug>` under `.claude/worktrees/`
   (gitignored). Merges into `ai/main` are `--no-ff` and gated on
   `npm run verify`. Promotion `ai/main → main` is a human-reviewed PR only.
3. **GitHub agent pipeline**: skipped for now; recorded as a TODO.md follow-up
   with a pointer to map-traverse's `docs/GITHUB_PIPELINE.md`.

## Components

- `AGENTS.md` — commands, architecture summary, backend/API facts, worktree
  workflow (solo + team invariants), solo agent loop, gotchas, skills index.
- `CLAUDE.md` — shim (see decision 1).
- `.agents/skills/` — `project-conventions`, `new-component`, `new-page`,
  `new-test`, `add-stac-endpoint`, `run-e2e`. The former `.claude/commands/`
  are deleted (skills are `/`-invocable in Claude Code; other harnesses read
  them natively).
- `.claude/prompts/ai-loop.md` — reusable orchestrator template that reads
  `PLAN.md`/`TODO.md` at runtime and classifies work as PARALLEL (agent team,
  one worktree per teammate) or SEQUENTIAL (solo, warm context), instead of
  map-traverse's hardcoded phase list.
- Root `npm run verify` script (app build + unit tests) — single verification
  entry point for every harness and teammate. `npx astro check` stays excluded
  until its OOM is fixed (TODO follow-up).
- `docs/AI-STRATEGY.md` — where canonical vs. harness-specific content lives.

## Project-specific adaptations (vs. map-traverse)

- **Singleton-resource rule**: dev server (:4321), pgstac (:8082), and the
  serial e2e suite are shared. Teammates run `npm run verify` only; the lead
  runs e2e once, after merging, on `ai/main`. (Analog of map-traverse's "no
  teammate runs Docker".)
- **npm lockfile protocol**: `git checkout --theirs package-lock.json &&
  npm install` — translation of the pnpm rule.
- **Hooks kept**: the committed `astro check` post-edit hook and the shadcn
  `components/ui/` guard are a strength map-traverse lacks; they stay in
  `.claude/settings.json` and are documented in the shim.

## Out of scope

GitHub Actions pipeline, MCP servers, `.claudeignore`, Storybook for map
components.
