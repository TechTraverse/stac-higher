> **Archived 2026-09-15.** The UI remodel queue is complete and the branch model
> this prompt describes is retired. Use `ai-loop.md` with a GitHub Issues queue.

# UI-remodel loop — session kickoff prompt

You are the UI-remodel session for stac-higher. Work the queue in
`app/UI-TODO.md` top-down: **first unchecked item, one slice per
iteration**, until the queue is done or you are blocked on the lead.

## Before the first slice

Read, in order: `AGENTS.md` (via CLAUDE.md), the `project-conventions`
skill, `docs/superpowers/specs/2026-08-31-ui-remodel-design.md` (settled
decisions — do not relitigate), `docs/decisions/0017-product-centric-ui-shell.md`,
and `app/UI-TODO.md` (constraints + verification protocol at the top).
The mockups are in `app/NOAA-geoplatform-design.zip` (unzip to a scratch
dir; screens in `handoff/screens/`, exact hex/layout in
`handoff/mockup-source.html`).

## Per-slice protocol

1. Worktree off `ai/main`: `git worktree add .claude/worktrees/<slug> -b
   ai/<slug> ai/main` (never branch from `origin/main`), `npm install` in
   the worktree.
2. Implement the slice per its UI-TODO entry. Use the `frontend-design`
   skill for aesthetic judgment; `npx shadcn@latest add` for primitives
   (hand-edits to `components/ui/` are hook-blocked).
3. `npm run verify` must pass.
4. Visual check: hold the dev server (:4321) and inspect every touched
   page in **both themes** in Chrome (claude-in-chrome or Playwright MCP),
   screenshot, self-critique against the mockup screens, fix, re-check.
5. Merge `--no-ff` to `ai/main`, remove the worktree, mark the item
   `- [x]`, append follow-ups to UI-TODO's bottom section. **Never push
   `ai/main` to origin.**

## Hard constraints

- Presentation layer only: no `/api/*` changes, no migrations, no route
  renames, no functionality removed. Settings visibility knob stays
  parenthesized (I-1).
- New dependencies: CodeMirror 6 (pin exact versions, note supply-chain
  review per I-65) and `@fontsource-*` fonts only.
- A parallel M3 session works `TODO.md`: do not touch `services/pipeline/`,
  `TODO.md`, or the M3 scoping notes. It owns Docker and the e2e suite —
  update e2e selectors in-slice, but **ask the lead before running e2e**.
- Subagents/teammates are fine for parallelizable work (per CLAUDE.md
  "Team tasks"), but singleton resources (dev server :4321, e2e) stay with
  this session's lead, and teammates run `npm run verify` only.

## When blocked

Stop and report: genuine merge contradictions, verify failures you cannot
fix on `ai/main`, anything requiring an `/api/*` change (propose it as a
follow-up instead), or a design question the brief doesn't settle.
