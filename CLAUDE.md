@AGENTS.md

# Claude Code specifics

Everything canonical lives in `AGENTS.md` (imported above) and `.agents/skills/`.
This file holds only what other harnesses can't use.

## Worktrees
`EnterWorktree` branches from `origin/main`, which is fine only if the local
`main` is current. Prefer creating manually off a freshly fetched `main`, then
enter by path:
`git fetch origin main && git worktree add .claude/worktrees/<slug> -b feat/<slug> origin/main`

## Automated hooks (`.claude/settings.json`, committed)
- **PostToolUse (Edit|Write)**: after any `.ts`/`.tsx`/`.astro` edit, a scoped
  `npx astro check --minimumSeverity error` runs in `app/`. If it reports
  errors, fix them before continuing.
- **PreToolUse (Edit|Write)**: edits to `components/ui/` are blocked. Use
  `npx shadcn@latest add <component>` instead.

These run without prompting. If a hook blocks an action, read its message — it
explains what to do instead.

## Team tasks
Each teammate gets its own worktree off `main` and exactly one issue. The lead
opens the PRs after teammates finish (full orchestrator prompt:
`.claude/prompts/ai-loop.md`). Coordination mechanism:
- `TaskCreate` one task per issue; create a final lead-integration task
  blocked by the workstream task IDs.
- Spawn each teammate via `Agent` with `run_in_background: true` and a
  self-contained prompt: its issue number, branch name, the plan to read, and
  the standing constraint "run `npm run verify` only — no e2e, no dev server,
  no Docker, no push".
- Teammates report on completion; don't poll. After each reports, the lead
  pushes the branch, opens the PR (`Closes #<n>`), runs the lead-only steps
  (e2e if UI flows changed, Docker measurement, live check) serially, and
  squash-merges. Later branches rebase onto `main` before their PR.
