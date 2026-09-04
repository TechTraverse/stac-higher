# Process compute — Kubernetes Jobs + Kueue, hardware profiles — design

**Date:** 2026-09-02
**Status:** **approved by the lead 2026-09-04** (the §13 decisions stand as
written unless overturned later; written from the 2026-09-02 direction
"plan on Kubernetes and Kueue"). The K queue in `TODO.md` is copied from
§14; K-1 may start. Migration numbering settled at approval: the X queue
(worked first) takes **028**, so K-3's ledger migration is **029**.
**Scope source:** ADR 0013 (executor isolation — the boundary this spec
works inside; its cloud-backend recommendation is superseded here), ADR 0018
(inputs + network profiles), ROADMAP §5.6 / §6.7 / §9 Phase 8, Phase 9 spec
§4, ISSUES I-61, I-81, I-90.
**Research inputs:** primary-source checks made 2026-09-02 (Kueue docs and
releases, Kubernetes release blogs, AWS EKS/EC2/GovCloud docs, NVIDIA GPU
Operator docs); the facts this spec leans on are cited inline and the
unverified ones are listed in §15.
**Decides:** ADR 0019. **Issues addressed:** I-61 (cloud half re-decided),
I-81 (cancel verb, K-4). **Issues opened:** I-92…I-96.

---

## 1. Why, in one page

Processes (Phase 9) run operator-authored code that will increasingly need
specific hardware — many CPU cores, tens of GB of memory, NVIDIA GPUs with
CUDA. Today a run is one Docker container on the pipeline host with a memory
limit and a wall-clock timeout, and nothing in the platform can say "this
needs a GPU" or put it on a machine that has one.

Two findings from reading the code today make the change larger than "add a
backend":

1. **The cloud backend ADR 0013 recommended cannot do this.** ECS/Fargate
   RunTask has no GPU support at all. GPU processes need EC2-backed
   capacity, which means either ECS-on-EC2, AWS Batch, or Kubernetes. This
   spec picks Kubernetes Jobs because they are the one primitive that is
   identical on EKS (commercial and GovCloud), AKS, GKE and on-prem, and
   because Kueue gives the SLURM-shaped part — quotas, priorities, "wait for
   capacity instead of failing" — as a standard controller on top.
2. **The executor blocks the worker.** `launch.py:182-184` calls
   `executor.launch` then `executor.wait` synchronously inside an async
   Procrastinate job; `wait` long-polls Docker in 5-second slices for up to
   `timeout_seconds` (max 86 400). With worker concurrency 1 (M3-D is still
   open) the platform executes exactly one run at a time and every ingest
   and delivery job queues behind it; at M3-D's concurrency 12 it would
   pin twelve worker slots per twelve runs. An hour-long GPU job cannot
   live inside a worker slot, and a pipeline redeploy must not kill it. The
   executor model has to become **submit, then reconcile** — a change the
   Kubernetes backend needs and the Docker backend benefits from.

What the operator gets: a **hardware profile** picker on the revision
(named tiers like `standard`, `large`, `gpu-l4`, `gpu-a100`), CPU/memory/GPU
sizing within the profile's bounds, a run ledger that shows *waiting for
capacity* honestly, and a cancel verb. What the deployment gets: profiles
are the portable vocabulary; each deployment maps them onto Kueue
ResourceFlavors and node pools (cloud) or Docker device requests and a
concurrency cap (compose). Nothing operator-facing names a cloud.

## 2. Gate (done-when)

Two legs, both required:

- **Local leg (compose, CPU only).** An operator deploys a revision on the
  `standard` profile through the UI, triggers a run, sees it move
  `queued → running → succeeded` with the same status vocabulary the cloud
  leg uses; deploys a second revision on a profile with `capacity: 1` while
  a run is in flight and sees the new run report **waiting for capacity**
  until the first finishes; cancels a running run from the UI and the row
  goes `cancelled` (audited) with the container gone. The pipeline worker
  is restarted mid-run and the run still completes and finalizes.
- **Cluster leg (EKS, GPU).** The same UI against an EKS deployment: a
  revision on `gpu-l4` whose code executes a CUDA kernel (cupy) and
  publishes an item; the run row shows *waiting for capacity* while
  Karpenter provisions a `g6` node, then runs and succeeds; a second
  `gpu-l4` run submitted while the quota is full waits (Kueue
  `QuotaReserved=False`, reason `Pending`) and admits when the first
  finishes; an `interactive` test run submitted behind a queue of
  triggered runs is admitted first. Evidence recorded M-gate style in
  ROADMAP §9.

A CI leg (kind, CPU-only, Kueue installed) runs the Kubernetes executor's
integration tests on every PR that touches it — see §8.

## 3. Hardware profiles — the portable vocabulary

### 3.1 Why named profiles

Every hosted job platform surveyed converges on **named accelerator tiers
plus CPU/memory counts** — Modal (`gpu="L4"`, `"H100:8"`), Flyte
(`Resources(gpu=1)` + `accelerator=A100`), Kubeflow Pipelines
(`set_accelerator_type`), Ray (`accelerator_type`), SageMaker's instance
enum. Raw Kubernetes selectors (`nodeSelector`, `podSpecPatch`) are the
escape hatch only in Kubernetes-native tools (Argo, Dagster). Two lessons
carried in: Modal's `A100` → `A100-80GB` auto-upgrade needed explicit
"exact vs at-least" suffixes, so a profile names **one** accelerator model;
Flyte's MIG partition tiers show GPU *slices* belong in the vocabulary as
their own profiles, not as a flag.

### 3.2 The profile document (cross-runtime contract)

One JSON document per deployment, read by **both** the app (write gate +
`GET /api/processes/hardware-profiles`) and the pipeline (launch-time
re-check + backend mapping). Path from `PROCESS_HARDWARE_PROFILES_FILE`;
default sets shipped in-repo at `infra/hardware-profiles/{local,kind,eks}.json`.
Golden fixture `tests/contract-fixtures/hardware-profiles.json` (new) pins
the shape for both readers.

```jsonc
{
  "version": 1,
  "profiles": [
    {
      "id": "standard",                       // stable; stored on revisions
      "label": "Standard",
      "description": "General purpose. Metadata extraction, small rasters.",
      "tier": "cpu",                          // cpu | cpu-large | gpu — UI grouping only
      "accelerator": null,
      "cpu":       { "min": 0.25, "max": 4,     "default": 1 },     // cores, fractional ok
      "memory_mb": { "min": 128,  "max": 16384, "default": 512 },
      "gpu_count": null,                      // null ⇒ no GPU field in the UI
      "max_queue_wait_seconds": 1800,
      "image": null,                          // null ⇒ the platform default runtime image
      "backend": { /* pipeline-only, §3.4 — the app ignores this block */ }
    },
    {
      "id": "gpu-l4",
      "label": "GPU — NVIDIA L4 (24 GB)",
      "description": "Single L4. Inference, cupy/numba kernels, medium models.",
      "tier": "gpu",
      "accelerator": { "vendor": "nvidia", "model": "l4", "memory_gb": 24 },
      "cpu":       { "min": 2,    "max": 16,    "default": 4 },
      "memory_mb": { "min": 4096, "max": 65536, "default": 16384 },
      "gpu_count": { "min": 1,    "max": 1,     "default": 1 },
      "max_queue_wait_seconds": 7200,
      "image": "process-runtime-cuda",        // §10 — the CUDA runtime image
      "backend": { }
    }
  ]
}
```

Rules:
- `id` is immutable once any revision references it; removing a profile
  from a deployment does not break stored revisions (immutable snapshots)
  — a run pinned to an unknown profile **dies** with a config error, the
  same outcome as `NetworkCapExceeded` today (`runner.py`).
- Exactly one profile is `"id": "standard"`. It is the default for every
  revision whose runtime carries no `hardware` block (all existing rows).
- Bounds are inclusive; the write gate clamps nothing — it rejects.
- `max_queue_wait_seconds` is the profile's promise to the credential
  minting path (§5.4) and the UI's "expected wait" hint.
- A profile's `image` is a **base**; the X-queue's `runtime.runtime_image`
  alias (X-3, landed 2026-09-04 as `"default" | "stactools"` on
  `runtimeLimits`, resolved through `PROCESS_RUNTIME_IMAGE*` at launch)
  selects the variant — `<base>-stactools` — so a GPU profile and the
  stactools library compose. K-1 adds `hardware` beside `runtime_image`;
  the executor resolves base then variant.

### 3.3 What the app exposes

`GET /api/processes/hardware-profiles` (member+): the profile list minus
`backend`, plus `{ "backend": "docker" | "kubernetes" }` so the UI can word
its wait states. No `PUBLIC_*` mirror this time — the profile set is a
document, not a scalar, and the `PROCESS_NETWORK_MAX` build-time mirror
already has a "swallow a bad value" wart (`ProcessDetailPage.tsx:223-235`).
The route reads the file at request time, cached per process lifetime.

### 3.4 The backend block (pipeline only)

```jsonc
"backend": {
  "kubernetes": {
    "queue": "process-runs",                       // LocalQueue name (per-group queues later, §7.4)
    "node_selector": { "stac-higher.io/flavor": "gpu-l4" },  // MUST equal the ResourceFlavor's nodeLabels
    "tolerations": [ { "key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule" } ],
    "runtime_class": null,                         // "nvidia" where the CNI/toolkit needs it (k3d)
    "extended_resources": { "nvidia.com/gpu": "gpu_count" }  // which resource carries the GPU count
  },
  "docker": {
    "device_requests": [ { "Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]] } ],
    "capacity": 1                                  // concurrent runs on this profile, this host
  }
}
```

The `node_selector` must equal the flavor's `nodeLabels`: Kueue picks a
flavor by matching the Workload's node selector against flavor labels and
injects the flavor's `tolerations` on admission (it does **not** add
tolerations for flavor `nodeTaints` — the pod must carry them, which is why
the profile lists them). Verified against the Kueue ResourceFlavor docs.

## 4. Runtime contract change

`app/src/lib/processes/schemas.ts` `runtimeLimits` (L130–137) gains one key,
so both runtime arms inherit it; `process/config.py`'s `ProcessRuntime`
gains the flattened fields; `process-runtime.json` gains cases.

```jsonc
"hardware": {
  "profile": "standard",     // must exist in the deployment's profile set
  "cpu": 1,                  // cores, within profile bounds
  "gpu_count": 0             // 0 unless the profile has an accelerator; then within bounds
}
```

- `memory_mb` and `timeout_seconds` stay where they are (the executor
  already consumes them) — the profile bounds `memory_mb` too. Moving
  memory under `hardware` would be tidier and is not worth touching every
  stored revision's reader for.
- **Lenient reader:** a runtime with no `hardware` block parses as
  `{profile: "standard", cpu: standard.cpu.default, gpu_count: 0}`. The
  Zod write gate defaults the same way; the Python reader must not require
  the block (every existing revision lacks it).
- **Dual enforcement**, copying the `PROCESS_NETWORK_MAX` pattern: the app
  route rejects a deploy whose profile is unknown or whose numbers are out
  of bounds (400 naming the bound); the pipeline re-validates at launch
  independently and a violation is a `dead` run with the reason. The
  fixture carries a sample profile set so both suites test bounds, not
  just structure.
- `RunSpec` (`executor.py:51-70`) gains `cpu: float`, `gpu_count: int`,
  `profile: HardwareProfile` (the resolved profile object, backend block
  included) and `priority: "interactive" | "triggered"`.

## 5. Executor model: submit, then reconcile

### 5.1 The interface

```
Executor.submit(spec: RunSpec) -> RunHandle           # create; returns immediately
Executor.status(handle) -> RunState                   # one call, never blocks
Executor.cancel(handle) -> None                       # idempotent; stop + mark
Executor.logs(handle, max_bytes) -> bytes             # unchanged (UNTRUSTED content)
Executor.reap(handle) -> None                         # unchanged
Executor.list_launched() -> list[LaunchedRun]         # unchanged; raises ExecutorUnavailable, never []
Executor.watch() -> AsyncIterator[RunEvent]           # optional; backend push of state changes
```

`RunState.phase ∈ {pending_capacity, starting, running, exited, lost}` with
`detail: str | None` (the backend's own words — the Kueue `Pending` message,
a Docker error) and `exit: ExitStatus | None` when `exited`. `wait()` is
removed from the ABC; the Docker backend's long-poll becomes its `watch()`
implementation (Engine `/events` filtered by the `stac-higher.run-id`
label).

### 5.2 Ledger changes (migration 029, app-owned per ADR 0001; 028 taken by X-4's builtin_id, 026 by W-2's retention cap, 027 by G-6's extractors, both of which landed first)

`process_runs` gains:

| column | type | meaning |
|---|---|---|
| `executor_backend` | text | `docker` / `kubernetes` — which backend holds the handle |
| `executor_handle` | text | backend-opaque id (container id / Job name); null before submit |
| `phase` | text CHECK | `pending_capacity` / `starting` / `running` / null — the sub-state of `running` |
| `phase_detail` | text | the backend's message for the phase (UI tooltip) |
| `submitted_at` | timestamptz | when `submit` returned; `started_at` now means the backend reported `running` |
| `cancel_requested_at` | timestamptz | set by the cancel verb; the watcher acts on it |

`status` CHECK adds **`cancelled`** (terminal). `TERMINAL_STATUSES` in
`ledger.py` becomes `("succeeded", "dead", "cancelled")`. The partial index
`process_runs_running_idx` already covers what the reconciler scans.

### 5.3 Flow

```
claim (unchanged: claim_run / claim_due_runs, status=running, attempts+1)
  → parse, network cap, HARDWARE CHECK (§4), plan + stage inputs (unchanged)
  → mint credentials (§5.4)
  → executor.submit(spec)  → write executor_handle, submitted_at, phase=pending_capacity
  → RETURN (the Procrastinate job ends here; no slot is held)

run watcher (singleton asyncio task in the worker, like the dispatcher listener)
  → for event in executor.watch(): update phase / phase_detail; on exited:
       logs → log_ref, reap, outcome_transition (unchanged), enqueue finalize
  → on cancel_requested_at: executor.cancel → logs → reap → status=cancelled

run tick (existing 1-minute recovery sweep) additionally:
  → for every status=running row with executor_handle: executor.status()
       and apply the same transitions — the backstop when the watcher missed
       an event or the worker restarted
  → a row whose credentials will expire before max_queue_wait: cancel +
       infrastructure_transition (back to queued, no attempt spent) — §5.4
```

Why a watcher and not polling: the G-3 latency posture (event-driven hops,
no minute-granular waits). Docker's events stream and a Kubernetes Job
`watch` are both push. The run tick remains the recovery path exactly as
G-3 made it for dispatch. HA posture unchanged: one watcher per worker is
safe because every transition is a compare-and-set on the row (I-40's
argument).

**Cancel (closes I-81).** `POST /api/processes/[id]/runs/[runId]/cancel`
(operator+, audited `cancel`) sets `cancel_requested_at` on a `queued` or
`running` row; a `queued` row flips to `cancelled` immediately (nothing was
submitted); a `running` row waits for the watcher to stop the backend and
capture logs. The OGC `dismiss` gap in ADR 0016 §3 closes with it.

### 5.4 Credentials outlive the queue wait — or the run requeues

Run-scoped STS credentials are minted **before** submit (they are in the
Job's environment) and last `max(900, timeout + grace)` today
(`credentials.py:161-163`). A run that waits two hours for a GPU would start
with expired credentials. Decision: mint for
`max_queue_wait_seconds + timeout_seconds + grace`, capped by the role's
maximum session duration, and have the run tick **cancel and requeue** (no
attempt spent) any run that is still `pending_capacity` when fewer than
`timeout_seconds + grace` remain on its credentials. The requeued run is
resubmitted with fresh credentials and — because Kueue orders by creation
time within a priority — loses its queue position. That is the honest
trade-off; the alternative (the run fetches credentials at start) would
put a credential-issuing endpoint inside the run's reach, which ADR 0013
forbids. In EKS the pipeline's own credentials come from Pod Identity /
IRSA, and `AssumeRole` from role credentials is role chaining with a
**one-hour** cap — so cloud profiles' `max_queue_wait_seconds` are bounded
by that unless the pipeline assumes the run role directly with web
identity. Tracked as I-93.

## 6. The Kubernetes executor

`services/pipeline/src/pipeline/process/kubernetes_executor.py`, selected by
`PROCESS_EXECUTOR=kubernetes` through a new `build_executor(settings)`
factory replacing the two inline `DockerExecutor(...)` constructions in
`jobs/process.py:179,305`. `/health` gains an `executor` key
(`{name, reachable, error}`) — the `Executor.name` field's documented intent
that was never wired.

### 6.1 One run = one Job + one Secret

Namespace `stac-higher-runs` (all runs; RBAC below). Per run:

- **Secret `stac-run-{run_id}`** carrying the entire `RunSpec.env` — it
  already holds the resolved secret-refs, the STS credentials and the code
  (`STAC_HIGHER_PROCESS_CODE_B64`), none of which may appear in a Job spec
  that anyone with `get jobs` can read. 1 MiB Secret cap ≫ the code field.
- **Job `stac-run-{run_id}`**: `backoffLimit: 0` (the platform owns retry
  — `outcome_transition`), `activeDeadlineSeconds: timeout_seconds`
  (Kueue resets the Job's start time on admission, so the deadline does not
  count the queue wait), `restartPolicy: Never`, `ttlSecondsAfterFinished`
  **unset** (reap is explicit, after logs — the ADR 0013 "AutoRemove not
  trusted" stance), `parallelism/completions: 1`. Labels:
  `stac-higher.run-id`, `stac-higher.process-id`,
  `kueue.x-k8s.io/queue-name: <profile.backend.kubernetes.queue>`,
  `kueue.x-k8s.io/priority-class: interactive|triggered`. Kueue suspends
  the Job through its webhook; the executor does not set `suspend` itself.
- **Pod template**: `envFrom: secretRef`; `resources.requests == limits`
  for cpu, memory and `nvidia.com/gpu` (extended resources require
  request = limit; Kueue treats limits as requests when requests are
  absent, but the executor sets both explicitly); `nodeSelector` and
  `tolerations` from the profile; `automountServiceAccountToken: false`;
  `securityContext`: `runAsNonRoot`, `allowPrivilegeEscalation: false`,
  `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`,
  `readOnlyRootFilesystem: true` with an `emptyDir` at `/tmp` and the
  work dir (sized by `ephemeral-storage` requests — a follow-on knob, not
  slice 1). Image from the profile or the platform default.
- **Handle** = the Job name. `status()` reads the Job (`.status.active /
  succeeded / failed`, conditions `Suspended`, `Complete`, `Failed`,
  `DeadlineExceeded`) and, while suspended, the Kueue Workload for the
  same Job (`QuotaReserved` reason + message → `pending_capacity` detail).
  `watch()` is a Job watch on the label selector plus a Workload watch;
  both reconnect with resourceVersion bookkeeping.
- **`logs()`**: `pods/log` of the Job's pod, capped at
  `process_log_max_bytes`; if the pod is gone the run records
  `error = "log unavailable"`. Streaming/rolling capture for long runs is
  I-94.
- **`cancel()`**: delete the Job with `propagationPolicy: Foreground`
  after reading logs; a suspended (never-admitted) Job has no pod and is
  simply deleted.
- **`reap()`**: delete Job + Secret. **`list_launched()`**: list Jobs by
  the run-id label; any API error raises `ExecutorUnavailable` (the
  reaper's "an outage is not no containers" rule, `reaper.py:73-80`).

### 6.2 Network isolation

A namespace-wide default-deny NetworkPolicy; the `isolated` level allows
egress only to the object-store endpoint (S3 VPC endpoint CIDR / MinIO
service) and DNS. Levels above `isolated` (ADR 0018) stay refused at the
write gate in this spec; when they land, `hosts` needs FQDN egress, which
plain NetworkPolicy cannot express — that is a CNI choice (Cilium
`toFQDNs`) or an egress proxy, decided then, not here.

### 6.3 Pipeline RBAC on the cluster

A ServiceAccount for the pipeline, bound to a **namespaced** Role in
`stac-higher-runs`: `jobs` create/get/list/watch/delete; `pods` get/list/
watch and `pods/log` get; `secrets` create/get/delete;
`workloads.kueue.x-k8s.io` get/list/watch;
`localqueues/pendingworkloads` (visibility API) get. Nothing cluster-wide;
nothing in any other namespace. The runs' pods have **no** ServiceAccount
token mounted.

## 7. Kueue topology

Kueue **v0.19.2** (2026-08-20), installed with server-side apply, on
Kubernetes **1.34+** (the versions Kueue tests against).

### 7.1 Objects (slice 1)

- One **ResourceFlavor per profile family**: `cpu-default` (no labels — any
  node), `gpu-l4` (`nodeLabels: {stac-higher.io/flavor: gpu-l4}`,
  `tolerations: [nvidia.com/gpu NoSchedule]`), `gpu-a100`, …
- One **ClusterQueue `process-runs`**, `queueingStrategy: BestEffortFIFO`
  (an unfit GPU job at the head must not block CPU jobs behind it), quotas
  per flavor for `cpu`, `memory`, `nvidia.com/gpu`, **nominal quota set to
  the node pool's ceiling** (§7.3 explains why that equality matters),
  `preemption.withinClusterQueue: LowerPriority` so an `interactive` test
  run can preempt a `triggered` run when the queue is full and nothing else
  fits (preemption evicts the victim — it goes back to the queue, the
  platform sees `Evicted` and requeues it without spending an attempt).
- One **LocalQueue `process-runs`** in `stac-higher-runs`.
- **WorkloadPriorityClass** `interactive` (test runs, value 1000) and
  `triggered` (value 100). Test-run responsiveness is now a scheduling
  property, not a warm-pool bypass — the Phase 9 spec §4 stance holds.
- `managedJobsWithoutQueueName: false` and a ValidatingAdmissionPolicy in
  `stac-higher-runs` rejecting label-less Jobs (Kueue's documented
  enforcement pattern), so nothing bypasses the queue by omission.

### 7.2 What the run sees

`QuotaReserved=False / Pending / "insufficient quota for nvidia.com/gpu in
flavor gpu-l4"` → `phase=pending_capacity`, `phase_detail` = that message,
and the visibility API's `positionInLocalQueue` for the UI ("3rd in
queue"). `Admitted=True` → `starting` until the pod runs. `Evicted=True`
(preempted / `waitForPodsReady` timeout) → requeue via
`infrastructure_transition`.

### 7.3 Capacity is Karpenter's, quota is Kueue's — they are not linked

**Verified 2026-09-02:** Karpenter does **not** implement the
ProvisioningRequest API that Kueue's autoscaler admission check drives
(cluster-autoscaler and GKE do). Karpenter's CapacityBuffer API
(alpha, v1.13+) is the intended replacement; Kueue issue #9662 tracks the
migration with no target release. On EKS today, then, Kueue admits a
workload against its *nominal* quota, the pod goes `Pending`, and
Karpenter reacts by provisioning a node. Consequences the deployment must
respect:

- ClusterQueue nominal quotas **must equal** the Karpenter NodePool
  `limits` for that flavor — quota above the pool's ceiling admits jobs
  that can never schedule.
- Kueue's `waitForPodsReady` setting (timeout + requeue) must be on, so an
  admitted job whose node never arrives (instance capacity, quota, an AMI
  problem) is evicted back to the queue rather than holding quota forever.
- Queue wait includes node provisioning: **2–5 minutes** for a GPU node
  from zero; a warm pool (NodePool minimum, or one long-lived GPU node)
  is a cost decision per deployment, recorded in the profile's
  `max_queue_wait_seconds`.

Tracked as I-92.

### 7.4 Per-group quotas (deferred, designed for)

Processes are group-owned; Kueue's natural mapping is one ClusterQueue per
group in a **Cohort** (hierarchical, `fairSharing.weight`, borrowing
limits — all GA in v0.17), each with a LocalQueue the profile's `queue`
field names. Slice 1 runs a single queue; the `queue` field and the
per-run `group` label are in place so the split is a manifest change and a
profile-file change, not a code change. I-96.

## 8. Local and CI parity

- **DockerExecutor** gains `NanoCpus` (from `cpu`), `DeviceRequests` (from
  the profile's docker block — only when the host exposes an NVIDIA
  runtime; Docker Desktop on macOS exposes **no** GPU and Apple Silicon has
  no CUDA, verified) and the `submit/status/cancel/watch` shape. Capacity:
  the claim path counts `running` rows on the same profile against
  `backend.docker.capacity`; when full the run is released back to
  `queued` with `phase=pending_capacity` and `next_attempt_at = now + 15 s`,
  no attempt spent — the UI shows exactly what the cluster leg would.
- **kind in CI** (`.github/workflows/`): a job that creates a kind cluster,
  installs Kueue, applies `infra/kueue/kind/` (CPU flavors only), and runs
  the Kubernetes executor's integration tests (submit → pending_capacity
  under a 1-CPU quota → admitted → exited; cancel; reap; list_launched
  under a broken API URL raises). No GPU in CI; the GPU path is covered by
  the profile mapping unit tests plus the cluster gate leg.
- **Linux dev with a GPU**: NVIDIA's `nvkind` (kind + CDI) works;
  `k3d` needs a custom CUDA-enabled K3s image and `runtimeClassName:
  nvidia` (the profile's `runtime_class` field exists for this). Neither
  is required for the gate.

## 9. UI

Conventions from the processes pages hold: plain `useState` forms, native
`<select>` with disabled options, TanStack mutations, server-side Zod
(`ProcessDetailPage.tsx` `CodeCard`, L237–402).

- **Hardware fieldset in the deploy form** (`CodeCard`): a profile
  `<select>` grouped by `tier` (`<optgroup>`: CPU / Large CPU / GPU), the
  selected profile's description under it with an accelerator badge
  (`NVIDIA L4 · 24 GB`); CPU and memory `<Input type="number">` whose
  `min`/`max`/`step` come from the profile (replacing the hard-coded `128`
  and `86400` literals); a GPU count input only when `gpu_count` is
  non-null; a one-line summary `4 vCPU · 16 GB · 1× L4 · waits up to
  2 h`. Changing the profile resets the numbers to the profile defaults.
  The current revision's `hardware`, `memory_mb` and `timeout_seconds`
  are synced into the form on load — today only `code` and `env` are
  (L263–273), so every deploy form silently starts at 512/900; that is
  fixed in passing.
- **Run rows** (`RunRow`, L728–807): `phase` chip — *Waiting for capacity*
  (with `phase_detail` and queue position in a tooltip), *Starting*,
  *Running*; hardware summary from the pinned revision; a **Cancel**
  button for `queued`/`running` (confirm dialog; the audited verb);
  `cancelled` gets its own badge variant.
- **Test run** (`TestRunCard`): submits with `priority: interactive`; the
  card's polling shows the same phases so a GPU test run that waits for a
  node says so rather than looking hung.
- **`/processes` list**: `health.ts`'s verdict treats `cancelled` as
  neutral (not a failure).
- **Profiles are read-only in the UI**: a deployment defines them; the
  operator picks. An admin profile editor is out of scope (§12).

## 10. The CUDA runtime image

`inline_python` runs on a platform-built image (ADR 0013 slice 1). GPU
profiles need a second image: `services/process-runtime/Dockerfile.cuda`
from `nvidia/cuda:<runtime>-ubuntu24.04` (CUDA 13 to match the EKS AL2023
driver 580 line), Python 3.12, the same blessed raster stack, plus **cupy**
and **numba** as the CUDA entry points. PyTorch is *not* in slice 1 —
multi-GB images and a supply-chain review of their own; a
`process-runtime-torch` variant is the recorded path when a process needs
it (I-95 records the demand signal). Both images build in
`containers.yml`, are pushed to the deployment's registry (ECR in GovCloud
— no ECR Public there, base images must be mirrored), and the profile's
`image` field selects them. The `container` runtime kind stays refused;
per-profile platform images cover CUDA without lifting it.

## 11. Cloud: AWS first, alternatives

### 11.1 EKS

- **EKS Auto Mode** for slice 1: managed Karpenter, NVIDIA driver and
  device plugin built into the node, available in **both GovCloud
  regions** since 2025-10. Its limits — no MIG, no time-slicing, no DRA —
  are acceptable for whole-GPU profiles; GPU sharing (a `gpu-l4-half`
  profile) needs a self-managed Karpenter NodePool with the device plugin
  in `mixed`/time-slicing mode, documented as the upgrade path.
- **NodePool per GPU flavor**, requirements on
  `eks.amazonaws.com/instance-gpu-name` (`l4`, `a100`, …) and
  `instance-gpu-count`, taint `nvidia.com/gpu: NoSchedule`, node label
  `stac-higher.io/flavor`, `limits` = the ClusterQueue quota (§7.3).
- **Profile → instance family** (verified availability, 2026-09-02):

| profile | GPU | AWS family | us-east-1 | us-gov-west-1 | us-gov-east-1 |
|---|---|---|---|---|---|
| `gpu-t4` | T4 16 GB | g4dn | yes | yes | yes |
| `gpu-l4` | L4 24 GB | g6 / gr6 | yes | yes | yes |
| `gpu-a10g` | A10G 24 GB | g5 | yes | **no** | **no** |
| `gpu-l40s` | L40S 48 GB | g6e | yes | **no** | **no** |
| `gpu-a100` | A100 40/80 GB | p4d / p4de | yes | p4d only | **no** |
| `gpu-h100` | H100 80 GB | p5 / p5en | yes | yes | **no** |
| `gpu-b200` | B200 | p6-b200 | yes | yes | yes |

  So the **portable GPU profile set is `gpu-t4`, `gpu-l4`, `gpu-b200`**
  (present in all three regions); `gpu-a100`/`gpu-h100` are west-only in
  GovCloud; the popular commercial families g5/g6e/g7 are absent from
  GovCloud entirely. A deployment's profile file lists only what its
  region has — this is exactly why profiles are per-deployment
  documents (I-95).
- **g7** (Blackwell RTX PRO) needs a driver the EKS AMIs do not ship yet;
  exclude it from any NodePool with automatic AMI selection.
- Spot for `triggered` runs (`karpenter.sh/capacity-type: spot`) is a
  cost option; interruption → `Evicted`-style requeue through the same
  path. Not in slice 1.
- App and pipeline placement is a **Phase 8 decision**: with a cluster
  already operated for runs, co-locating them on EKS (separate
  namespaces) is likely simpler than ECS/Fargate alongside; ROADMAP
  Phase 8 records the question.

### 11.2 Alternatives (same profiles, different backend block)

| need | AWS | Azure | GCP | on-prem |
|---|---|---|---|---|
| cluster | EKS (Auto Mode) | AKS (node auto-provisioning; managed GPU pools in preview) | GKE (Standard/Autopilot; official Kueue tutorials, flex-start via ProvisioningRequest) | any 1.34+ cluster |
| GPU node selection | `eks.amazonaws.com/instance-gpu-name` | `karpenter.azure.com/sku-gpu-*` | `cloud.google.com/gke-accelerator` | your labels |
| autoscaler ↔ Kueue | none (Karpenter; §7.3) | none (Karpenter-based) | ProvisioningRequest (`queued-provisioning.gke.io`) | cluster-autoscaler `check-capacity` |
| managed batch without k8s | AWS Batch (in GovCloud; Batch-on-EKS uses `nvidia.com/gpu` limits) | Azure Batch / Container Apps Jobs | Cloud Batch | — |
| registry | ECR (GovCloud: no ECR Public) | ACR | Artifact Registry | any OCI |
| compliance | GovCloud | Azure Government | Assured Workloads (GKE listed FedRAMP High, IL4/5) | — |

## 12. What this spec explicitly does NOT do

- Lift the `container` runtime refusal (user-supplied images).
- Network levels above `isolated` on Kubernetes (FQDN egress is a CNI
  decision; ADR 0018's cap machinery is unchanged).
- Per-group Kueue quotas / fair sharing (§7.4, I-96) — designed for, not
  built.
- GPU sharing profiles (MIG / time-slicing) and DRA (`resource.k8s.io/v1`
  GA in 1.34; NVIDIA's DRA driver 0.5.0 marks full-GPU allocation GA but
  MIG/time-slicing alpha and a cluster runs either DRA or the device
  plugin, not both). Slice 1 is the classic device plugin.
- Multi-node / gang-scheduled runs (Volcano / JobSet). One run = one pod.
- An admin UI for editing profiles.
- Cost accounting per run.
- Log streaming for long runs (I-94).
- Deciding where the app and pipeline themselves run in Phase 8.

## 13. Decisions taken without the lead — confirm or overturn

1. **Profiles are deployment config, not operator-editable data.** A JSON
   file read by both runtimes, like `PROCESS_NETWORK_MAX`, rather than an
   app-owned table with admin CRUD. Reversible later (the API shape does
   not change); chosen because a profile change is a capacity change an
   operator cannot make anyway.
2. **`memory_mb` stays top-level** in the runtime; only `hardware`
   (`profile`, `cpu`, `gpu_count`) is new.
3. **One ClusterQueue, two priority classes** in slice 1; per-group queues
   deferred.
4. **EKS Auto Mode** over self-managed Karpenter, accepting whole-GPU-only.
5. **cupy + numba in the CUDA image, no PyTorch** in slice 1.
6. **Credential-expiry requeue** rather than in-run credential fetch
   (§5.4).
7. **The K queue starts after G-6/G-7** (same files, same agent) and K-4
   coordinates with M3-D (both change the worker's job model).
8. **Cancel lands in K-4**, closing I-81 as part of the executor change
   rather than as its own slice.

## 14. Slices (the K queue, copied into TODO.md)

- **K-1 · Hardware-profile contract + runtime `hardware` block.** §3, §4.
  `hardware-profiles.json` fixture + loader in both runtimes; `runtimeLimits`
  gains `hardware`; `ProcessRuntime` gains the flattened fields; write-gate
  and launch-time bounds checks; `RunSpec` gains `cpu`, `gpu_count`,
  `profile`, `priority`; `infra/hardware-profiles/local.json`;
  `GET /api/processes/hardware-profiles`. No executor change yet.
- **K-2 · Hardware picker in the UI.** §9. `CodeCard` fieldset, revision
  sync fix, run-row hardware summary; `docs/processes.md` "Hardware"
  section. Depends on K-1.
- **K-3 · DockerExecutor honours the profile.** §8. `NanoCpus`,
  `DeviceRequests`, per-profile capacity → `pending_capacity` requeue
  (needs the `phase` columns — lands the migration 029 columns except
  `cancelled`). Depends on K-1.
- **K-4 · Submit-then-reconcile executor model + cancel.** §5. New ABC,
  Docker `watch()` off Engine events, the run watcher singleton, run-tick
  reconcile, credential-expiry requeue, `cancelled` status + the audited
  cancel verb + UI button (closes I-81). Coordinate with M3-D. Depends on
  K-3.
- **K-5 · KubernetesExecutor + `build_executor` + `/health` executor key.**
  §6. Job + Secret per run, status/watch/cancel/logs/reap/list_launched,
  RBAC manifests under `infra/kubernetes/`. Unit tests against a fake API
  server; the kind integration suite lands in K-6. Depends on K-4.
- **K-6 · Kueue manifests + kind CI leg.** §7, §8. `infra/kueue/`
  (flavors, ClusterQueue, LocalQueue, priority classes, admission policy,
  `waitForPodsReady`), `infra/hardware-profiles/kind.json`, the CI job.
  Depends on K-5.
- **K-7 · CUDA runtime image.** §10. `Dockerfile.cuda`, `containers.yml`,
  a cupy smoke process in `pipeline.demo`. Independent of K-4…K-6.
- **K-8 · EKS deployment (Phase 8 work).** §11. Auto Mode cluster, GPU
  NodePools per flavor with limits = quotas, ECR mirrors,
  `infra/hardware-profiles/eks.json`, NetworkPolicy, pipeline
  ServiceAccount + Pod Identity. Depends on K-5, K-6, K-7.
- **K-9 · Gate rehearsal (LEAD ONLY).** §2. Both legs; evidence in
  ROADMAP §9; close I-61's cloud half with the measured queue-wait and
  cold-start numbers.

## 15. Risks and issues to open

- **I-92 · Kueue quota and Karpenter capacity are not linked** (§7.3):
  quotas must mirror NodePool limits by hand; `waitForPodsReady` is the
  safety net. Revisit when Karpenter's CapacityBuffer path lands in Kueue
  (#9662).
- **I-93 · Run credentials vs. queue wait** (§5.4): role chaining caps
  sessions at one hour in EKS; long GPU queues will requeue and lose
  position. Measure in K-9; the fix is either a web-identity assume of the
  run role or a shorter wait promise.
- **I-94 · Run logs are captured at exit only**: a pod lost to node
  failure loses its log; hour-long runs have no live tail.
- **I-95 · GovCloud GPU families lag commercial**: no g5/g6e/g7; profile
  files are per-region; PyTorch demand for the CUDA image is tracked here.
- **I-96 · Per-group quotas deferred**: one shared queue means one group
  can starve another until the cohort split lands.
- **Unverified (do not build on):** whether Kueue's `waitForPodsReady`
  interacts cleanly with Karpenter's provisioning latency at the timeouts
  we'd set; Spot availability and interruption rates for GPU families in
  GovCloud; Azure Government GPU SKUs (third-party listing only).
