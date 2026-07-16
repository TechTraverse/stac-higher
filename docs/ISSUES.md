# Outstanding issues

Known gaps, residual risk, and deferrals — tracked honestly so they aren't mistaken for "done." Status: 🔴 open · 🟡 accepted/mitigated · 🟢 resolved · ⚪ deferred-by-design.

Each entry: what it is, why it exists, and where it's tracked. Close an entry by moving it to 🟢 with the resolving commit/PR, or delete it once shipped and documented elsewhere.

---

## Carried-forward work (committed, not yet done)

### I-1 · Per-collection read-visibility at the proxy 🔴
Phase 1 delivered authenticated transactions + audience validation, but **read-visibility filtering** (different groups see different collections) cannot be done with auth-proxy config alone — it needs OPA or a custom filter factory. Deferred out of Phase 1.
- Tracked in: [ADR 0002](decisions/0002-auth-proxy-enforcement.md); Phase 1 note in [`../ROADMAP.md`](../ROADMAP.md).
- Blocks: fully multi-tenant read isolation.

---

## Known limitations / residual risk (accepted, mitigated)

### I-2 · DNS-rebind residual for TLS endpoints 🟡
SFTP, plain FTP, and S3-over-http pin the resolved IP (rebind-proof). **FTPS control channels and S3-https keep the hostname** so TLS cert validation / SNI works, leaving a narrow DNS-rebind window between the egress check and the TLS connect. Mitigated by a fail-closed `resolve_pinned` recheck immediately before connect; the FTP PASV data-channel redirect is fully closed regardless.
- Tracked in: comments in `services/pipeline/.../adapters/{ftps,s3}.py`; found in the Phase 2 adversarial review.

### I-3 · Drain latency is ~1 minute, not ~10s 🟡
ADR 0004 targets a ~10s test-connection turnaround, but Procrastinate's periodic scheduler is **1-minute-granular**. The drain runs every minute and clears the whole pending backlog each tick, so worst-case start latency is ~60s. A true sub-minute drain needs a NOTIFY-woken consumer.
- Tracked in: [ADR 0004](decisions/0004-app-pipeline-bridge.md) "Revisit"; comment in `services/pipeline/.../jobs/drain.py`.

### I-4 · Adapter `list/get/put/delete` are mock-tested only 🟡
The full `StorageAdapter` interface is implemented, but only `test()` is exercised live (via the drain job). `list/get/put/delete` are covered by unit tests with mocked clients — no live-server integration yet. Phase 3 (asset service) will be their first real consumer.
- Tracked in: here; `services/pipeline/tests/test_adapters.py`.

### I-5 · Zod v4 ↔ zodResolver `as any` cast 🟡
Form resolvers use an `as any` cast due to a Zod v4 / `@hookform/resolvers` type-inference mismatch. Known pattern, not a bug — don't "fix."
- Tracked in: `AGENTS.md` "Gotchas"; `project-conventions` skill.

---

## Test & infra gaps

### I-6 · FTPS not live-testable on arm64 🟡
The `ftps-test` server (`fauria/vsftpd`) is amd64-only and crashes under Rosetta on Apple Silicon. FTPS shares the `FtpAdapter` code path (only the TLS upgrade differs), which the live FTP test exercises, and is unit-tested — but FTPS-specific live validation needs an amd64 host.
- Tracked in: header comment in `infra/compose.test-servers.yml`; Phase 2 note in [`../ROADMAP.md`](../ROADMAP.md).

### I-7 · Pipeline jobs error noisily before tables exist ⚪
On a fresh DB the drain/health-sweep jobs log `UndefinedTable` each tick until the app's migration middleware creates `stac_higher.connections`/`connection_checks`. Harmless (they recover once tables exist) and correct per ADR 0001 (pipeline never creates tables), but noisy in a pipeline-first startup.
- Tracked in: here. Workaround for local pipeline-only testing: apply migration 004 first.

### I-8 · Full-project `npx astro check` OOMs 🟡
A pre-existing Vite/rolldown plugin type conflict between the repo root and `app/node_modules` OOMs a full-project `astro check`. Rely on `npm run verify` (build + vitest) and the scoped PostToolUse `astro check` hook instead.
- Tracked in: `AGENTS.md` "Gotchas".

---

## Deferred by design (later phases)

### I-9 · KMS credential provider ⚪
Credentials use a local `CREDENTIALS_MASTER_KEY` behind an `EncryptionProvider` seam. A KMS-backed provider arrives in Phase 8. — `app/src/lib/connections/crypto.ts`.

### I-10 · `stac-api` connection protocol ⚪
Reserved in the enum; create/update reject it and the adapter factory raises `NotImplementedError("reserved for a future release")`.

### I-11 · Audit-log partitioning & retention ⚪
`stac_higher.audit_log` is append-only and unpartitioned. Phase 6 adds time-partitioning + a compliance-driven retention job (partition maintenance must drop/re-create the append-only triggers per partition). — migration 003 comment.

### I-12 · `connection_checks` accumulation ⚪
Test-result rows are never pruned; a partial index keeps the drain's pending scan cheap, but the table grows. Retention/GC is Phase 6 hygiene.
