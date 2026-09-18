# AI Strategy — Context Engineering Across Harnesses

This repo works with any AI coding agent (Claude Code, Codex, OpenCode, Cursor,
Copilot, …). Instead of per-harness instruction files, we standardize on two
open formats and keep the per-harness footprint to the minimum shim each tool
requires. 

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
CONTRIBUTING.md            ← the human entry point: issue → branch → PR → review
.claude/worktrees/         ← AI worktrees (gitignored)
opencode.json              ← opencode permissions (bash deny-list)
.opencode/plugin/          ← opencode hook equivalents (astro check, shadcn guard)
.opencode/agent/           ← opencode subagent definitions (teammate)
.opencode/command/         ← opencode slash commands (/solo-task, /team-task)
docs/superpowers/specs/    ← approved design specs (milestone/slice scope sources —
docs/superpowers/plans/       each queue's epic issue cites its spec); plans are
                              their step-by-step implementation breakdowns
```

The specs/plans track is part of the AI surface: a milestone is scoped by
writing a dated spec there (brainstorm → approved design), the queue's `epic`
issue names it as the scope source, and the solo loop reads the spec section
an issue cites before starting it. **The work queue itself is GitHub Issues**
(a milestone + `queue: <name> (<code>)` label per queue, `ready`, `blocked`,
`lead-only`, `migration`; one `epic`
per queue) — the issue carries everything an agent needs to start, so no
backlog file has to be told to it.

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
   seven skills and the AGENTS.md content. For opencode:
   `opencode run "list your project skills"` — and note that opencode does NOT
   hot-reload config, so restart it after touching `opencode.json`,
   `.opencode/**`, or a skill.

## Branch model (summary — full rules in AGENTS.md)

Trunk-based: `main` is the only long-lived branch. Every issue runs in a
worktree branch `feat/<slug>` (or `fix/`, `docs/`) under `.claude/worktrees/`
and lands through a squash-merged PR into `main`; CI (verify, pipeline tests,
Storybook) gates the PR. Nobody — human or agent — commits to `main`
directly. Singleton resources (dev server :4321, pgstac :8082, the load
harness, the serial e2e suite) are owned by whoever leads the integration —
parallel teammates run build + unit tests only. (The former local-only
`ai/main` integration branch was retired 2026-09-15; references to it in
dated specs, plans and ROADMAP history are historical.)

## What's deliberately harness-specific

- **`.claude/settings.json`** — permission allowlist plus two hooks: a scoped
  `astro check` after TS/TSX/Astro edits, and a guard that blocks hand-edits to
  `components/ui/`. Other harnesses: replicate as desired; not required.
- **`.claude/prompts/ai-loop.md`** + `CLAUDE.md` "Team tasks" — orchestration
  uses Claude-only tools. The invariants (worktrees off `main`, one issue per
  PR, lead runs the singletons) are in `AGENTS.md` and apply to every harness.
- **`opencode.json` + `.opencode/**`** — opencode's equivalents, because
  opencode reads none of `.claude/`:
  - `opencode.json` — a bash **deny-list** (`rm -rf`, `git reset --hard`,
    force-push; `ask` on pushes matching `main` and on `docker compose down -v`).
    Deliberately not a port of Claude's allowlist: an allowlist would prompt on
    everything unlisted (`uv run pytest`, `docker buildx bake`, …) and stall the
    autonomous loops. Note opencode evaluates the **last** matching rule, so
    broad patterns come first.
  - `.opencode/plugin/guards.js` — the two hooks: refuse `edit`/`write` to
    `components/ui/`, and run the app-scoped `astro check` after `.ts`/`.tsx`/
    `.astro` edits under `app/` or `packages/shared/`. Plain JS to avoid adding
    `@opencode-ai/plugin` as a dependency. It resolves the checkout root from
    the edited file, so edits inside `.claude/worktrees/<slug>/` get checked in
    the right `app/`.
  - `.opencode/agent/teammate.md` — the parallel-worker subagent. Stronger than
    the Claude equivalent: Docker, the dev server, e2e, `git merge` and
    `git push` are denied at the permission layer, so the singleton rule is
    enforced rather than merely requested.
  - `.opencode/command/{solo,team}-task.md` — `/solo-task <queue>` and
    `/team-task <queue>`. Kept thin on purpose: they defer to `AGENTS.md` and
    carry only what it doesn't (the PARALLEL/SEQUENTIAL heuristic, subagent
    dispatch, stop conditions). Taking the queue as an argument removes the
    "which queue?" failure mode structurally.
