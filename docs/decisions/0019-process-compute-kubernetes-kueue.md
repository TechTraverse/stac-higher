# ADR 0019 — Process compute: Kubernetes Jobs + Kueue, hardware profiles as the portable vocabulary

- **Status:** accepted (2026-09-04 — the lead approved the process-compute
  design spec `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md`;
  proposed 2026-09-02; implemented by the K queue in `TODO.md`)
- **Supersedes, in part:** ADR 0013's "Recommended backend pair" — the
  cloud half (ECS/Fargate RunTask primary, K8s-Job-on-EKS fallback). Every
  isolation invariant in ADR 0013 stands unchanged; only the cloud backend
  choice and the executor's blocking shape move.
- **Related:** ADR 0014 (output path — unchanged), ADR 0018 (inputs and
  network profiles — the network levels map onto NetworkPolicy), ADR 0016
  (the `dismiss` gap closes with the cancel verb), ISSUES I-61, I-81, I-90,
  I-92…I-96.

## Context

Processes will run code that needs specific hardware: many cores, tens of
GB of memory, NVIDIA GPUs with CUDA. ADR 0013 chose container-per-run
behind an `Executor` seam and recommended ECS/Fargate RunTask as the cloud
backend. Two facts established 2026-09-02 make that recommendation
unusable for this requirement and the current executor shape untenable:

1. **Fargate has no GPU support.** GPU capacity on AWS means EC2-backed
   nodes — ECS-on-EC2, AWS Batch, or Kubernetes.
2. **The executor blocks the worker.** `execute_run` calls
   `executor.wait` synchronously inside an async Procrastinate job; at the
   current worker concurrency of 1 the platform runs one process at a
   time and stalls ingest and delivery behind it, and a worker restart
   kills the run. Hour-long GPU jobs cannot live inside a worker slot.

The platform's constraints are unchanged: AWS first with GovCloud as the
accredited target, portability to other clouds and on-prem, local dev as a
single `docker compose up`, and a FISMA-High-bound isolation boundary.

## Options

### A. AWS Batch (managed queues over ECS-EC2 or EKS)

- **For:** managed GPU compute environments, queues, priorities, retries;
  available in both GovCloud regions with no differences; no cluster to
  operate.
- **Against:** AWS-only — the Azure/GCP equivalents (Azure Batch, Cloud
  Batch) are different APIs, so portability would be one backend per
  cloud with divergent semantics; queue position and wait reasons are
  less observable than Kueue's Workload conditions.

### B. Kubernetes Jobs + Kueue — **chosen**

- **For:** `batch/v1 Job` is the one primitive identical on EKS
  (commercial and GovCloud), AKS, GKE and on-prem. Resource requests plus
  node selection put a run on the right node; Karpenter (or the cloud's
  equivalent) scales GPU nodes from zero. Kueue supplies the SLURM-shaped
  layer as a standard controller: per-flavor quotas, priority classes,
  preemption, fair sharing, "wait for quota instead of failing", and
  observable wait reasons (`QuotaReserved=False / Pending / insufficient
  quota for nvidia.com/gpu in flavor …`) plus a queue-position API. The
  isolation invariants map one to one: NetworkPolicy for the restricted
  network, pod limits + `activeDeadlineSeconds` for the run budget, a
  per-run Secret for the environment, no ServiceAccount token in the pod.
- **Against:** a cluster to operate (EKS Auto Mode reduces this — managed
  Karpenter, NVIDIA driver and device plugin built in, available in
  GovCloud); Karpenter does **not** implement Kueue's ProvisioningRequest
  admission check, so Kueue quota and physical capacity are linked by
  hand (I-92); the executor must become asynchronous either way.

### C. SLURM — dismissed

The right tool for tightly coupled multi-node MPI on a shared HPC cluster.
Platform runs are independent single-container jobs triggered by catalog
events — the Kubernetes Job shape. A `SlurmExecutor` wrapping `sbatch` /
`squeue` fits the same seam if a partner site demands it; nothing here
forecloses it.

## Decision

### Hardware profiles are the platform vocabulary; backends translate

Operators pick a **named hardware profile** (`standard`, `large`, `gpu-l4`,
`gpu-a100`, …) and size CPU, memory and GPU count within the profile's
bounds. A profile names one accelerator model (no "at least" semantics —
the Modal lesson) and GPU slices are their own profiles. Profiles are a
**per-deployment document** read by both the app (write gate, UI listing)
and the pipeline (launch-time re-check, backend mapping) — the
`PROCESS_NETWORK_MAX` dual-enforcement pattern — with a golden contract
fixture. Nothing operator-facing names a cloud, an instance type or a
Kubernetes selector; the profile's pipeline-only `backend` block carries
the Kueue LocalQueue, node selector and tolerations (cloud) or Docker
device requests and a concurrency cap (compose).

### The executor is submit-then-reconcile

`Executor.submit / status / cancel / logs / reap / list_launched`, with an
optional push `watch()`. The Procrastinate job ends at `submit`; a
run-watcher singleton in the worker applies backend events to the ledger
and the one-minute run tick reconciles stragglers via `status()`. The
ledger records the backend and handle, a `phase` sub-state
(`pending_capacity` / `starting` / `running`) with the backend's own
message, and a `cancelled` terminal status behind an audited cancel verb.
Runs survive worker restarts; no worker slot is held for a run's duration.

### Kubernetes backend shape

One Job + one Secret per run in a dedicated namespace; the Job carries the
Kueue queue label and a priority-class label (`interactive` for test runs,
`triggered` otherwise); `backoffLimit: 0` (the platform owns retry),
`activeDeadlineSeconds` = the revision timeout, explicit reap after logs.
Pod: requests = limits, `automountServiceAccountToken: false`,
non-root, no capabilities, `RuntimeDefault` seccomp, read-only root, the
profile's node selector and tolerations. The pipeline's ServiceAccount has
a namespaced Role only. Kueue: a ResourceFlavor per profile family, one
ClusterQueue whose nominal quotas equal the node pools' ceilings, one
LocalQueue, `waitForPodsReady` on, a ValidatingAdmissionPolicy rejecting
label-less Jobs. Per-group ClusterQueues in a Cohort are the designed-for
upgrade.

### Cloud order

AWS first: EKS Auto Mode, one Karpenter NodePool per GPU flavor with
`limits` equal to the Kueue quota, ECR (mirrored base images in GovCloud).
The portable GPU profile set across us-east-1 and both GovCloud regions is
`gpu-t4` (g4dn), `gpu-l4` (g6), `gpu-b200` (p6-b200); `gpu-a100` / `gpu-h100`
exist in us-gov-west-1 only; g5 / g6e / g7 are absent from GovCloud. Azure
(AKS node auto-provisioning) and GCP (GKE, with native ProvisioningRequest
support for Kueue) reuse the same profiles with a different backend block.

## Invariants (new; ADR 0013's all carried)

- **A revision's hardware never exceeds its profile's bounds, and a
  profile the deployment does not define is a dead run** — enforced at the
  app's write gate AND at launch, independently.
- **Nothing operator-facing names a cloud, an instance type or a
  Kubernetes selector.** Profiles are the only hardware vocabulary in
  revisions, the API and the UI.
- **A run never holds a pipeline worker slot for its duration.** Submit
  returns; completion is observed, never awaited in-job.
- **Kueue nominal quota never exceeds the node pool's provisioning
  ceiling** for the same flavor (I-92) — a quota the autoscaler cannot
  honour admits jobs that never schedule.
- **A run's credentials are minted for its promised queue wait plus its
  timeout; a run that would start with insufficient lifetime is requeued
  without spending an attempt.** No credential-issuing endpoint is ever
  reachable from inside a run (ADR 0013).

## Consequences

- ADR 0013's Fargate cold-start discussion is moot; the new latency figure
  is GPU node provisioning from zero (minutes) versus a warm pool (seconds)
  — a per-deployment cost decision recorded in the profile's
  `max_queue_wait_seconds`.
- The executor interface changes shape (`wait` removed); the Docker backend
  is rewritten to the same submit/watch model, which is also what makes
  M3-D's worker concurrency safe for processes.
- A second platform runtime image (CUDA) enters the supply-chain review
  surface; user-supplied images remain refused.
- Phase 8 gains a Kubernetes cluster to deploy from IaC and an open
  question of whether the app and pipeline co-locate on it.
- Test-run responsiveness becomes a scheduling property (priority class +
  preemption), not a warm-pool bypass — the Phase 9 spec §4 stance holds.

## Revisit

- When Kueue supports Karpenter's CapacityBuffer API (kueue#9662): link
  quota to capacity and drop the hand-mirrored limits invariant.
- If a process needs multi-node or gang scheduling: JobSet / Volcano on
  the same cluster, a new runtime kind, its own ADR.
- If DRA (`resource.k8s.io/v1`) and the NVIDIA DRA driver reach GA for
  MIG/time-slicing: GPU-slice profiles move from device-plugin `mixed`
  mode to ResourceClaims.
