# ADR 0013 — Process executor isolation

**Status:** proposed (Phase 9 / M5 — not accepted; to be settled by the Phase 9 design spec)

## Context

Phase 9 (ROADMAP §6.7, §9) introduces **processes**: user-defined
transformations whose code — `inline_python` stored platform-side, or a
user-supplied `container` image — runs inside the platform's data plane.
That code is **untrusted relative to the platform**: it is written by
operators, not by us, and a bug or a hostile snippet must not be able to
read platform credentials, reach the database, bypass the egress policy, or
write the catalog and canonical storage directly (the output path is ADR
0014's subject).

The platform's posture raises the stakes: gov/defense-adjacent, FISMA High
as the eventual operating environment (ROADMAP §2), with an existing
security model built on exactly this kind of boundary — credentials decrypt
only in the pipeline at job time (§5.2), all egress goes through the
connections policy (`resolve_pinned`, §5.5), every privileged verb is
audited. The executor must not be the hole in that model.

Deployment reality constrains the options: local dev is a single
`docker compose up` (docker socket availability inside the pipeline
container is not a given), and cloud is AWS/GovCloud (ECS/Fargate or EKS —
no docker socket at all on Fargate).

## Options

### A. In-worker subprocess

Run user code as a subprocess of the pipeline worker (resource-limited via
rlimits/cgroups where available).

- **For:** trivial to build; no new infrastructure; the cheapest path to a
  demo. Works identically in compose and in any cloud runtime.
- **Against:** effectively **no isolation** — same filesystem, same network
  namespace, same environment as the worker that holds decrypted platform
  credentials and the DB connection string. Only acceptable behind an
  explicit "operators are trusted" posture plus the egress policy, and that
  posture contradicts the reason this ADR exists. Any acceptance of A must
  be scoped as a dev/demo mode, never the deployed default.

### B. Container-per-run behind an executor interface — **recommended direction**

Define an `Executor` interface (launch, stream logs, enforce limits, reap);
back it with docker-out-of-docker locally and an ECS/Fargate task or K8s Job
in cloud. This mirrors the Phase-0 queue-interface pattern
(Procrastinate/SQS) exactly: business logic sees one seam, deployments pick
a backend.

- **For:** a real security boundary (own filesystem, own network namespace —
  attachable to a restricted egress network), natural fit for the
  `container` runtime kind (inline_python becomes a platform-built base
  image + mounted code), per-run memory/timeout limits are first-class in
  every backend, and the interface keeps local dev honest without foreclosing
  GovCloud.
- **Against:** the most engineering: image plumbing, per-backend quotas and
  cold-start latency (Fargate task start is seconds-to-a-minute — fine for
  batch runs, felt on interactive test runs), docker socket exposure to the
  pipeline container locally (its own hardening question), and a per-backend
  compatibility matrix to test.

### C. MicroVM (Firecracker et al.) — noted and dismissed

Stronger isolation than containers, but operationally heavy (host kernel
requirements, no managed GovCloud-portable service equivalent in our stack)
and disproportionate to the current threat posture, where the code author is
an authenticated, audited operator — not an anonymous internet user. Revisit
only if the platform ever runs code from outside the operator trust
boundary.

## Invariants (carried regardless of option)

- User code never sees decrypted platform credentials, the platform DB, or
  the platform's own object-store keys. Secret-ref env values (§5.6) resolve
  at launch into the executor's environment only; the run receives
  **short-lived, run-scoped** credentials for its staging prefix (ADR 0014)
  and nothing else.
- All network egress from user code obeys the connections egress policy — a
  `resolve_pinned` analog at the executor's network boundary, plus the
  documented deployment network policy (§5.5).
- Resource limits (memory, timeout) are enforced by the executor, not
  trusted to the code; a run that exceeds them fails the run, it does not
  degrade the worker.
- Run logs are captured to object storage and referenced from
  `process_runs.log_ref` — never interleaved into the pipeline's own logs as
  trusted content.

## Consequences

- **B** adds the first component whose local and cloud implementations
  differ *operationally* (not just by config): the compose stack must grant
  the pipeline a way to launch sibling containers, and the cloud stack needs
  task-definition plumbing + quota headroom (ISSUES I-61).
- The executor interface becomes a security-critical seam and belongs in the
  FISMA control-mapping inventory (ROADMAP "Beyond the phases").
- Interactive "test run" UX inherits the backend's cold-start latency;
  the design spec should decide whether test runs may use a warmer path
  (e.g. a pooled local executor) without weakening the boundary.

## Revisit

Accept/revise this ADR in the Phase 9 design spec, after the I-61
investigation (docker socket locally, Fargate quotas/latency in GovCloud)
has produced concrete backend candidates for the executor interface.
