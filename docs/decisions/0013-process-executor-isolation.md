# ADR 0013 — Process executor isolation

**Status:** accepted (2026-08-29, by the Phase 9 design spec — Option B, `DockerExecutor` locally per the P9-A investigation; cloud backends in Phase 8)

> **Amended 2026-09-02 by [ADR 0019](0019-process-compute-kubernetes-kueue.md):** the
> cloud-backend recommendation below (ECS/Fargate RunTask primary, K8s Job
> fallback) is **superseded** — Fargate has no GPU support, and GPU/CUDA
> processes are now a requirement. The cloud backend is Kubernetes Jobs +
> Kueue on EKS; the executor seam becomes submit-then-reconcile. Every
> isolation invariant in this record stands unchanged.

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

## Slice-1 scope (settled 2026-08-27)

The first M5 slice supports **`inline_python` only, executed on a
platform-built base image** — the `container` runtime kind (user-supplied
image references) is a later slice. This keeps user-image supply-chain
review, registry policy, and image-scanning machinery out of the first
accreditation surface while exercising the full executor boundary; the
`runtime.kind` contract shape (§5.6) already carries `container` so nothing
is foreclosed. The build order also runs local-first (ROADMAP §9 steering
order): the local docker backend is implemented first, cloud backends are
paper-investigated (P9-A, I-61) and built only in Phase 8.

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

## Investigation (P9-A, 2026-08-29 — I-61)

### Local backend: docker-out-of-docker, measured

Hands-on experiments against the dev machine's Docker (throwaway containers;
the compose pipeline container itself confirmed to have neither
`/var/run/docker.sock` nor a docker CLI today):

- **Sibling-launch viability**: a container with the socket mounted launches
  sibling containers without privilege beyond the socket itself. Warm-image
  wall times: **0.66s** via the docker CLI, **0.125s** create→exit via the
  raw Engine API over the unix socket (the docker-py-shaped path the Python
  pipeline would use). Cold start is dominated by image pull — moot for
  slice 1's pre-pulled platform-built executor image.
- **Limits are first-class at create time**: `HostConfig.Memory`,
  `NanoCpus`, and `NetworkMode` verified in one API call — including
  `NetworkMode=none` (no network at all) and, for the egress-policy analog,
  attachment to a dedicated restricted network instead.
- **Socket exposure hardening**: `tecnativa/docker-socket-proxy` verified as
  a least-privilege boundary: with `CONTAINERS=1 POST=1` and everything else
  off, container create/start/wait/logs work while `exec` and `volumes`
  return 403. The pipeline would get `DOCKER_HOST=tcp://socket-proxy:2375`
  and never the raw socket — the proxy (an HAProxy allowlist) becomes the
  auditable seam. Residual risk to document: create/start with arbitrary
  `HostConfig` still permits bind-mounts and privileged flags, so the
  EXECUTOR must be the only proxy client and the compose network must keep
  the proxy unreachable from user-code networks.

### Cloud backends: paper findings

- **ECS/Fargate (GovCloud)**: ECS and Fargate-on-ECS are available in both
  GovCloud (US) regions (Fargate since 2019). Task cold start is
  **~30–45s typical, 20–60s unoptimized** (ENI attach + image pull dominate;
  SOCI lazy loading cuts pull time ~50–60%). Quotas are vCPU-based
  ("Fargate On-Demand vCPU"); new accounts start low (~6 vCPUs) and raise
  via Service Quotas — a deployment-checklist item, not a blocker.
- **EKS / K8s Job (GovCloud)**: EKS is available in GovCloud but
  **EKS-on-Fargate is not** — a K8s Job backend there means managed EC2
  node groups (capacity pre-provisioned; job start then measures in seconds,
  at the cost of running nodes and a much larger operational surface).

### Recommended backend pair

`DockerExecutor` (Engine API via a least-privilege socket proxy, restricted
egress network, per-run Memory/NanoCpus/timeout) for compose/dev — built in
M5 slice 1; **ECS/Fargate RunTask** as the cloud backend candidate for
Phase 8 (paper-only until then), with K8s-Job-on-EKS the fallback if a
deployment already operates EKS. The interface consequence stands: test-run
UX inherits Fargate's ~30–45s cold start in cloud, so the spec should keep
interactive "test run" on a warmer path or set expectations in the UI.

Sources: AWS re:Post on Fargate provisioning; AWS ECS task-launch
optimization docs; AWS what's-new (Fargate in GovCloud, 2019; vCPU-based
quotas, 2022); AWS re:Post on EKS-with-Fargate in GovCloud.

## Revisit

Accept/revise this ADR in the Phase 9 design spec. The I-61 investigation
above has produced the concrete backend pair; remaining for the spec: the
executor interface signature, the socket-proxy compose wiring, and the
test-run warm-path question.
