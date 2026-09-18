# Contributing

How work flows through this repo, for people and for the AI agents they drive.
The rules agents follow are in `AGENTS.md`; this page is the human entry point.

## The loop

1. **Pick an issue.** The backlog is GitHub Issues. Filter by queue and
   readiness: `gh issue list --label "queue: k8s compute (K)" --label ready`,
   or open the queue's milestone in the web UI. The pinned **Start here**
   issue lists every queue with its code, purpose and spec. Read the queue's `epic` issue first — it holds the
   spec to read, the settled decisions and the ordering — then the issue.
   Assign yourself so nobody else starts it.
2. **Branch off `main`** in a worktree so several tasks can coexist:
   ```
   git fetch origin main
   git worktree add .claude/worktrees/<slug> -b feat/<slug> origin/main
   cd .claude/worktrees/<slug> && npm install
   ```
   Prefixes: `feat/`, `fix/`, `docs/`.
3. **Do the work**, yourself or with an agent. With Claude Code: open the
   worktree, and say "work issue #<n>" — `AGENTS.md` tells it the rest. Other
   harnesses (Codex, opencode, Cursor) read the same `AGENTS.md` and skills;
   see `docs/AI-STRATEGY.md`.
4. **Gate locally**: `npm run verify` at the repo root. If the pipeline
   changed, also `uv run pytest` and `uv run ruff check .` from
   `services/pipeline/`.
5. **Open the PR** to `main`:
   ```
   git push -u origin feat/<slug>
   gh pr create --base main --title "K-2: hardware picker in the UI" --body "Closes #10 ..."
   ```
   Say which gates ran, which lead-only steps remain (e2e, Docker, live
   check), and any deviation from the plan. CI runs verify, the pipeline tests
   against a real pgstac, and the Storybook build.
6. **Review and merge.** One review from someone other than the author when
   the change is non-trivial. Merges are squash-only and the branch is
   deleted automatically. `git worktree remove .claude/worktrees/<slug>`.

## Labels

| Label | Meaning |
|---|---|
| `queue: <name> (<code>)` | Which work queue, e.g. `queue: k8s compute (K)`. One milestone and one `epic` issue per queue carry its context. |
| `ready` | Unblocked; anyone may take it. |
| `blocked` | The body names the blocking issues. Flip to `ready` when they close. |
| `lead-only` | Needs the shared singletons (Docker stack, e2e suite, load harness, standing demo) or a cloud account. |
| `migration` | Reserves a `stac_higher` migration number; read the body before adding one. |
| `agent:go` | Reserved for the Claude GitHub app: label an issue to have it implemented automatically (not installed yet). |

## Queues

Work is grouped into queues. Each is a milestone (progress, description), an
`epic` issue (spec to read, settled decisions, slice order) and a label. Issue
titles end with a slice code such as `(K-3)`: the letter is the queue, the
number the slice's position in its spec. Specs, plans and `docs/FEATURES.md`
cite those codes, which is why they stay.

| Queue | Code | What it is | Status |
|---|---|---|---|
| NOAA-scale readiness | M3 | ~60 catalog items/s sustained, measured, no lost items | active — epic #1 |
| Process compute on Kubernetes | K | Runs as Kubernetes Jobs via Kueue, hardware profiles, non-blocking executor | active — epic #2 |
| Built-in extractors | X | Curated stactools packages as one-click extractors | live gates only — epic #3 |
| GOES GeoColor demo | G | The standing NODD → COG → deliver → tiles demo loop | complete; owed live looks |
| Container images | C | Bring-your-own process images + scanning | spec pending (PR #23) |
| Ingest window | W | Date window, prefix expansion, retention cap | complete |
| Item lineage | D | `derived_from` links on process outputs | complete |
| Map page | V | Products as map layers on one time axis | complete |
| Pipeline graph | P | Lineage lines + full graph view | complete |

The pinned "Start here" issue on GitHub is the same table with links; keep the
two in step when a queue opens or closes.

## Things only one person can run at a time

The dev server (`:4321`), the Docker stack (`:8082` and friends), the load
harness and the Playwright e2e suite share one database. Coordinate before
running them, and never run two at once. Agents working in parallel run
`npm run verify` only; the lead runs the rest before merging.

## Adding work

- A new slice of an approved spec: open an issue in the queue, link the spec
  section, add it to the epic's list, label it `ready` or `blocked`.
- A limitation or deferral, not a task: `docs/ISSUES.md`.
- A significant, hard-to-reverse choice: an ADR under `docs/decisions/`.
- A new area of work: write the design spec under `docs/superpowers/specs/`
  first (brainstorm → approved), then an epic and its slices.

## Where the knowledge lives

- `AGENTS.md` — the always-loaded rules for agents (and a good summary for people).
- `.agents/skills/` — task playbooks (`project-conventions`, `new-page`, `run-e2e`, …).
- `docs/` — features, ADRs, known issues, specs and plans (`docs/README.md` is the index).
- `docs/backend.md` — the local stack, ports, credentials.
