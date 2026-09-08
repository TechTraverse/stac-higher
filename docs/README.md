# Documentation

Project documentation for STAC Higher, organized into three tracks:

| Track | Where | What it holds |
|---|---|---|
| **Features** | [`FEATURES.md`](FEATURES.md) | Catalog of what's built, per delivery phase — status, entry points, and links to the detailed reference doc for each area. |
| **Decision records** | [`decisions/`](decisions/README.md) | Architecture Decision Records (ADRs): one file per significant, hard-to-reverse choice, with context and consequences. |
| **Outstanding issues** | [`ISSUES.md`](ISSUES.md) | Carried-forward work, known limitations / residual risk, deferrals to later phases, and test/infra gaps. Fully-resolved entries move to [`ISSUES-ARCHIVE.md`](ISSUES-ARCHIVE.md) (one-line stubs remain). |
| **Design specs & plans** | [`superpowers/`](superpowers/) | Dated, approved design specs (milestone/slice scope sources — `TODO.md` cites the active one) and their implementation plans. |

Delivery is planned in phases — see [`../ROADMAP.md`](../ROADMAP.md) for the full plan and the live phase-status table.

## Reference docs (detailed, per area)

- [`backend.md`](backend.md) — the local stack (services, ports, credentials, auth-enforced overlay), app environment variables, and the full Astro API route table with access rules.
- [`auth.md`](auth.md) — OIDC login, claims mapping, dev-bypass, RBAC & audit (Phase 1).
- [`connections.md`](connections.md) — connections data model, credential encryption, and the `/api/connections` surface (Phase 2).
- [`monitoring.md`](monitoring.md) — flow telemetry, alerts, notification channels, the `/monitoring` UI, retention & GC, metrics (Phase 6 / M2).
- [`serving.md`](serving.md) — OGC API serving (titiler-pgstac + tipg), the per-collection serving toggle, the item and collection raster previews, and its I-1/I-68 caveats (pre-M5 hardening).
- [`push-ingest.md`](push-ingest.md) — the external push-client contract: bearer auth, staged uploads, the brokered write path, finalize semantics and rejection reasons (Phase 7).
- [`processes.md`](processes.md) — writing a process: the run environment, how to publish items, and the rules that surprise (Phase 9).
- [`AI-STRATEGY.md`](AI-STRATEGY.md) — how `AGENTS.md`, the skills, and per-harness shims fit together for AI coding agents.

## Conventions

- **New feature** → add a row to [`FEATURES.md`](FEATURES.md); write the deep reference as `docs/<area>.md` and link it.
- **Significant decision** → add the next-numbered ADR under [`decisions/`](decisions/README.md) and register it in that index.
- **Known gap / limitation / deferral** → add an entry to [`ISSUES.md`](ISSUES.md) with its status and where it's tracked.
- Keep this folder honest: record residual risk and deferrals, not just the happy path.
