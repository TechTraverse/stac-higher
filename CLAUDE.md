@AGENTS.md

# Claude Code specifics

Everything canonical lives in `AGENTS.md` (imported above) and `.agents/skills/`.
This file holds only what other harnesses can't use.

## Worktrees
`EnterWorktree` branches from `origin/main` by default — wrong for this repo.
Create manually off `ai/main` first, then enter by path:
`git worktree add .claude/worktrees/<name> -b ai/<name> ai/main`

## Automated hooks (`.claude/settings.json`, committed)
- **PostToolUse (Edit|Write)**: after any `.ts`/`.tsx`/`.astro` edit, a scoped
  `npx astro check --minimumSeverity error` runs in `app/`. If it reports
  errors, fix them before continuing.
- **PreToolUse (Edit|Write)**: edits to `components/ui/` are blocked. Use
  `npx shadcn@latest add <component>` instead.

These run without prompting. If a hook blocks an action, read its message — it
explains what to do instead.

## Team tasks
Each teammate gets its own worktree off `ai/main`. The lead merges all branches
into `ai/main` after teammates finish (full orchestrator prompt:
`.claude/prompts/ai-loop.md`). Coordination mechanism:
- `TaskCreate` one task per workstream; create a final lead-integration task
  blocked by the workstream task IDs.
- Spawn each teammate via `Agent` with `run_in_background: true` and a
  self-contained prompt: its branch name, the plan section to read, and the
  standing constraint "run `npm run verify` only — no e2e, no dev server, no
  Docker".
- Teammates report on completion; don't poll. After all report, the lead merges
  `--no-ff` in order, resolves conflicts per AGENTS.md, then runs verify (and
  e2e if UI flows changed) serially on `ai/main`.
