# AI Strategy — Context Engineering Across Harnesses

This repo works with any AI coding agent (Claude Code, Codex, OpenCode, Cursor,
Copilot, …). Instead of per-harness instruction files, we standardize on two
open formats and keep the per-harness footprint to the minimum shim each tool
requires. (Pattern adopted from the map-traverse repo, 2026-07.)

## The two standards

| Standard | What it covers | Spec |
|---|---|---|
| **AGENTS.md** | Project instructions: commands, architecture rules, git workflow, gotchas | [agents.md](https://agents.md/) |
| **Agent Skills** | On-demand task playbooks: one folder per skill with a `SKILL.md` | [agentskills.io](https://agentskills.io/) |

`AGENTS.md` is always-loaded context — keep it terse, facts only. Skills load
only when relevant (agents see just `name`/`description` until a task matches),
so long-form procedures, templates, and checklists belong there.

## Where things live

```
AGENTS.md                  ← canonical instructions (every harness)
.agents/skills/<name>/     ← canonical skills (every harness)
CLAUDE.md                  ← shim: "@AGENTS.md" import + Claude-only content
.claude/skills             ← symlink → ../.agents/skills (Claude Code discovery)
.claude/settings.json      ← Claude Code permissions + hooks (astro check, shadcn guard)
.claude/prompts/ai-loop.md ← Claude Code multi-agent orchestrator prompt
.claude/worktrees/         ← AI worktrees (gitignored)
```

## Rules of the road

1. **Canonical content goes in `AGENTS.md` or a skill — never in a
   harness-specific file.** About to add a rule to `CLAUDE.md`? Stop: it belongs
   in `AGENTS.md` (a fact every agent needs) or a skill (a procedure loaded on
   demand).
2. **Harness-specific files may only contain what that harness alone can use**:
   permissions, hooks, tool-specific orchestration (Claude's team choreography).
3. **Facts vs. procedures**: a one-line constraint ("never hand-edit
   `components/ui/`") → `AGENTS.md`. A multi-step playbook with templates
   ("add a page") → a skill.
4. **Don't duplicate.** One canonical home each; everything else references it.
   The former `.claude/commands/` were folded into skills for this reason —
   skills are `/`-invocable in Claude Code and readable by every other harness.
5. **After changing agent config** (AGENTS.md, skills, symlink), smoke-test
   discovery headlessly: `claude -p "list your project skills"` should show the
   six skills and the AGENTS.md content.

## Branch model (summary — full rules in AGENTS.md)

AI work lives on `ai/main`; every task runs in a worktree branch `ai/<slug>`
under `.claude/worktrees/`. Verify (`npm run verify`) gates every merge into
`ai/main`. Humans promote via PR `ai/main → main`; the AI never commits to
`main`. Singleton resources (dev server :4321, pgstac :8082, the serial e2e
suite) are owned by whoever leads the merge — parallel teammates run build +
unit tests only.

## What's deliberately harness-specific

- **`.claude/settings.json`** — permission allowlist plus two hooks: a scoped
  `astro check` after TS/TSX/Astro edits, and a guard that blocks hand-edits to
  `components/ui/`. Other harnesses: replicate as desired; not required.
- **`.claude/prompts/ai-loop.md`** + `CLAUDE.md` "Team tasks" — orchestration
  uses Claude-only tools. The invariants (worktrees off `ai/main`, lead merges,
  singletons rule) are in `AGENTS.md` and apply to every harness.
