# K-5 · Kubernetes Executor and the Executor Factory — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub issue #13 (K-5) · epic #2 · **no migration** (029 = K-3, 030 = C-1, 031 = K-4, 032/033 = Z-2; K-5 writes only columns 029/031 already created — see Ruling K5-R16). Closes the pipeline half of I-160.

**Goal:** With `PROCESS_EXECUTOR=kubernetes` every process run and image scan runs as one Kubernetes Job (plus one Secret) in `stac-higher-runs`, admitted by Kueue, reported through K-4's `status()`/`watch()` contract — waiting for capacity, starting, running, exited, lost — cancelled, logged and reaped exactly as the Docker backend is; one `build_executor(settings)` factory replaces every inline `DockerExecutor(...)`, and `/health` names the executor and whether it is reachable.

**Architecture:** A new `KubernetesExecutor` (`process/kubernetes_executor.py`) implements K-4's ABC over a small stdlib client (`process/kube_api.py`: urllib for REST, an asyncio TLS stream for watches — the Docker backend's no-SDK precedent). `submit` creates the Job (Kueue's webhook suspends it), then a Secret holding the COMPLETE run environment with an ownerReference to the Job, so the environment never appears in the Job spec and dies with the Job. `status()` reads the Job, then — only when it needs to — the Kueue Workload (by the `kueue.x-k8s.io/job-uid` label) and the Job's newest pod, and maps them onto K-4's five phases (the table in Task 6). `watch()` merges a Job watch (label `stac-higher.run-id`) and a Workload watch into K-4's `RunEvent`s, resuming from the last resourceVersion/bookmark and ending on `410 Gone` so K-4's `run_watcher` reconnects with a RESYNC. The run's deadline is on the **pod** (`activeDeadlineSeconds = timeout`), so a timed-out or cancelled pod is killed but kept and its log stays readable; reap is a foreground delete of the Job and its Secret(s). Least-privilege RBAC lives in `infra/kubernetes/`, and the unit tests' fake API server **enforces** it, so the manifests are proven sufficient by the suite and necessary by a coverage test.

**Tech Stack:** Python 3.12 (urllib + `ssl`, asyncio streams, `http.server` for the test fake), pytest, PyYAML (already in the lock, tests only); Kubernetes `batch/v1` Jobs, `v1` Secrets/Pods, Kueue `Workload` (`kueue.x-k8s.io`); Helm chart env (`infra/helm/stac-higher/`), vitest for the chart and the app route. **No new dependency** (K5-R1).

**Spec:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §6 (all), §3.4 (the `backend.kubernetes` block), §5 (the executor model K-4 builds), §7.1–7.3 (what Kueue does to a Job), §8 (CI parity — the kind leg is K-6), §13; ADR 0019, ADR 0013 (isolation invariants, "AutoRemove not trusted"), ADR 0021 (scans run as platform runs), ADR 0024 (deployment artifacts). Deployment spec `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §4.1, §5 (registry mirror), §6.1, §6.5, §8 (on `origin/main`). Predecessor plan, **the contract**: `docs/superpowers/plans/2026-11-25-k4-submit-reconcile-cancel.md`. ISSUES I-93, I-94, I-123, I-124, I-151, I-160, I-169.

## Global Constraints

- **Worktree:** `.claude/worktrees/k5-kubernetes-executor`, branch `feat/k5-kubernetes-executor`. Task 0 rebases it onto `origin/main` once K-4 (#12) has merged; this plan was written before K-3/K-4 code existed on `main`. `npm install` at the worktree root; `uv sync` in `services/pipeline/`.
- **Gates:** pipeline tasks `uv run pytest -q` and `uv run ruff check .` from `services/pipeline/`; app/chart tasks `npm run verify` from the worktree root (the chart's vitest files skip without `helm` — say so if they skipped). Teammates never run e2e, the dev server, Docker, kind, a cluster or image builds, and never push.
- **Namespace:** `stac-higher-runs` (setting `PROCESS_K8S_NAMESPACE`, chart `processRuns.namespace`). Everything the executor creates, reads or deletes is in it; nothing cluster-scoped, nothing in another namespace.
- **Names:** Job `stac-run-{run_id}-{hex6}` (process) / `stac-scan-{scan_id}-{hex6}` (scan), `hex6 = secrets.token_hex(3)` per submission (K5-R2); env Secret = the Job's name; pull Secret = `{job}-pull` (only for a user image with a registry credential); handle = the Job name.
- **Labels (Job, pod template, Secrets):** `stac-higher.run-id`, `stac-higher.run-kind` (`process`|`image_scan`), `stac-higher.process-id` (process) or `stac-higher.image-id` (scan). **Job only:** `kueue.x-k8s.io/queue-name: <profile queue | PROCESS_K8S_DEFAULT_QUEUE>`, `kueue.x-k8s.io/priority-class: <spec.priority>` (`interactive`|`triggered`). The executor never sets `spec.suspend` (Kueue's webhook does).
- **Job spec:** `backoffLimit: 0`, `completions: 1`, `parallelism: 1`, `activeDeadlineSeconds: timeout_seconds + 600` (`JOB_DEADLINE_SLACK_SECONDS`), `ttlSecondsAfterFinished` unset, `podFailurePolicy` unset.
- **Pod spec:** `restartPolicy: Never`; `activeDeadlineSeconds: timeout_seconds` (K5-R4); `terminationGracePeriodSeconds: 10`; `serviceAccountName: stac-higher-run` (setting `PROCESS_K8S_RUN_SERVICE_ACCOUNT`); `automountServiceAccountToken: false`; `enableServiceLinks: false`; annotation `karpenter.sh/do-not-disrupt: "true"`; `securityContext: {runAsNonRoot: true, runAsUser: 10001, runAsGroup: 10001, fsGroup: 10001, seccompProfile: {type: RuntimeDefault}}`; one container `run` with `securityContext: {allowPrivilegeEscalation: false, privileged: false, readOnlyRootFilesystem: true, capabilities: {drop: [ALL]}}`, `envFrom: [{secretRef: {name: <job>}}]` and **no `env`**, `resources.requests == resources.limits` = `cpu: "<round(cpu*1000)>m"`, `memory: "<memory_mb>Mi"`, plus `<gpu resource>: "<gpu_count>"` when `gpu_count > 0`; `emptyDir {medium: Memory, sizeLimit: "<memory_mb>Mi"}` at `/tmp` always and at the platform workdir (`/home/runner` for a platform process image, `/home/scanner` for a scan); `nodeSelector`, `tolerations`, `runtimeClassName` from the profile's `backend.kubernetes`; `imagePullSecrets: [{name: <job>-pull}]` only with a pull Secret.
- **Secrets:** `type: Opaque`, `immutable: true`, `data` = base64 of every `RunSpec.env` value, `ownerReferences: [{apiVersion: batch/v1, kind: Job, name, uid, controller: false, blockOwnerDeletion: false}]`. Never logged, never in an exception message, never in the Job.
- **Kueue API:** `KUEUE_API_VERSION = "v1beta2"` (Task 0 confirms what Kueue v0.19.2 serves; K-6 confirms on kind). Workloads are found by label `kueue.x-k8s.io/job-uid=<Job uid>`.
- **Settings (pipeline `Settings`, all read in `from_env`):** `PROCESS_EXECUTOR` (`docker` default | `kubernetes`; anything else fails startup), `PROCESS_K8S_NAMESPACE` (`stac-higher-runs`), `PROCESS_K8S_API_URL` (default `https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT`, IPv6 bracketed; `http://` only to a loopback host), `PROCESS_K8S_TOKEN_FILE` (`/var/run/secrets/kubernetes.io/serviceaccount/token`), `PROCESS_K8S_CA_FILE` (`/var/run/secrets/kubernetes.io/serviceaccount/ca.crt`), `PROCESS_K8S_RUN_SERVICE_ACCOUNT` (`stac-higher-run`), `PROCESS_K8S_DEFAULT_QUEUE` (`process-runs`), `PROCESS_K8S_REGISTRY_MIRRORS` (JSON object `{"<registry host>": "<mirror prefix>"}`, default `{}`).
- **Constants:** `JOB_DEADLINE_SLACK_SECONDS = 600`, `TERMINATION_GRACE_SECONDS = 10` (= K-4's `STOP_GRACE_SECONDS`), `START_FAILURE_GRACE_SECONDS = 300`, `CANCEL_POD_DEADLINE_SECONDS = 1`, `WATCH_TIMEOUT_SECONDS = 300` (server-side `timeoutSeconds`), `WATCH_IDLE_SECONDS = 360` (client read timeout), `DETAIL_MAX_CHARS = 500`, `REQUEST_TIMEOUT_SECONDS = 10`, `EXECUTOR_PROBE_TTL_SECONDS = 30`.
- **Do not touch:** K-4's ledger SQL and invariants I1–I14, the Reconciler's decision table (except the one change K-4 is asked to make up front, see "Asks of K-4" — if K-4 did not make it, Task 0 makes it here), `finalize/*`, `delivery/`, the migrations, `components/ui/`. Structured logging: data in `extra={...}`, constant messages; never `filename`, `module`, `name`, `msg`, `args`, `levelname` as `extra` keys.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  ```

## What K-5 relies on from K-4 (verify each in Task 0)

K-4 is planned, not merged. Each line is a name, signature or behaviour K-5 is written against; Task 0 checks it in the merged code and adapts the named task if K-4 shipped something different.

| # | K-4 contract point (plan reference) | K-5 use |
|---|---|---|
| C1 | `Executor` ABC: abstract `submit, status, cancel, logs, reap, list_launched`; `wait` gone; optional `watch() -> AsyncIterator[RunEvent]` with `supports_watch` true iff overridden; `submitted_phase: ClassVar[str] = "pending_capacity"` (Task 3) | the class K-5 implements; K-5 keeps the default `submitted_phase` |
| C2 | `RunState(phase, detail=None, exit=None)`, phases `ledger.EXECUTOR_PHASES = (pending_capacity, starting, running, exited, lost)`, `exit` set iff `exited` (Tasks 1, 3) | `status()` return type |
| C3 | `RunEvent(run_id, handle_id=None, kind="process")`, `RESYNC = RunEvent(run_id=None)` (Task 3) | `watch()` yields these |
| C4 | `status()` raises `ExecutorUnavailable` when the backend cannot be asked; `lost` only on affirmed absence (Task 3) | Job 404 ⇒ `lost`; any other API error raises |
| C5 | `cancel()` = idempotent stop, resources kept for `logs`/`reap` (Task 3) | K5-R5: pod-deadline patch keeps the pod and its log |
| C6 | Reconciler table rows 1–6: cancel row calls `status()` → `cancel` → `collecting` (logs → finish) → `reap` after commit; row 3 credential requeue on `pending_capacity`; row 4 `exited` → `outcome_transition`; row 5 `lost` → requeue, attempt restored; every executor call via `asyncio.to_thread` (Task 5) | K-5's states feed exactly these rows |
| C7 | `Reconciler.on_event`: RESYNC → `sweep(limit=RECONCILE_BATCH)`; `kind != "process"` dropped; else `reconcile_id(run_id)`; `reconcile_id` returns `None` for a row of another backend (Task 5) | scan Job events need no filtering in K-5 |
| C8 | `run_watcher(watch, on_event, *, reconnect_delay=5.0, max_connects=None)`: each connect expects RESYNC first; a raising stream is logged and reconnected (Task 6) | `watch()` raises to force a RESYNC reconnect |
| C9 | `build_reconciler(queue, settings, *, executor=None)`, `build_run_watcher(queue, settings)` (waits forever when not `supports_watch`), `build_cancel_listener(queue, settings)` in `jobs/process.py` (Tasks 5, 6) | Task 8 points all three at `build_executor` |
| C10 | `submit_run(executor, settings, *, …) -> SubmittedHandle`; `run_one` maps submit exceptions: `ImagePullFailed`/`ImageMissing` → attempt spent; `HardwareUnavailable`/`CredentialBudgetExceeded`/`SecretResolutionError` → dead; `ExecutorUnavailable`/`RunCredentialsError` → `requeue_without_attempt` (Task 5 Step 4) | K-5's `RunRejected` subclasses `HardwareUnavailable` so no mapping changes |
| C11 | `record_submission(..., backend=executor.name, phase=executor.submitted_phase, ...)`; `list_submitted_runs(backend=...)` filters on `executor_backend` (Task 2) | backend `"kubernetes"` |
| C12 | `run_to_exit(executor, handle, timeout_seconds, *, poll_seconds=2.0, ...)`; `execute_scan` = `submit` + `run_to_exit` + `logs` + `reap` (Task 3, R11) | scans on Kubernetes go through it unchanged |
| C13 | Credentials last `max(900, max_queue_wait + timeout + grace)`; `RunCredentials.expires_at` (Task 4) | the requeue rule covers K-5's pending states (K5-R9) |
| C14 | Docker: `_iter_body_lines(reader, *, chunked)` (module-level in `docker_executor.py`), `STOP_GRACE_SECONDS = 10`, the OOM wording in `DockerExecutor.status` (Tasks 3, 6) | Task 3 moves the line reader to a shared module; Task 6 reuses the OOM text |
| C15 | `MemoryExecutor` fields `states`, `default_states`, `submit_error`, `status_error`, `submitted`, `cancelled`, `reaped`, `status_calls` (Task 3) | Task 8's capacity-gate tests |
| C16 | `DEFAULT_REAP_MAX_AGE_SECONDS` unchanged; K-4 Task 10 residual (e) hands its widening to K-5 | Task 8 |
| C17 | K-3: `run_one` checks `docker_backend(profile).capacity` for every executor (`runner.py`, "K-3 (spec §8): a profile's local capacity") | Task 8 gates it to backends that enforce local capacity |

## Asks of K-4 (cheaper before K-4 is implemented)

1. **Row 5's error should carry the backend's words.** K-4 fixes the `lost` error text (`"the executor lost the run (its container is gone)"`). K-5 reports two different `lost`s — a deleted Job and a disrupted pod (node failure, eviction) — and an operator needs to tell them apart. Change row 5 to `error = state.detail or LOST_ERROR`. If K-4 merged without it, Task 6 Step 5 makes the one-line change and its test.
2. **Put the HTTP stream reader in a shared module.** `_iter_body_lines` and the status-line/header parse inside `DockerExecutor.watch` are transport-agnostic; K-5 needs them over TLS. If K-4 creates `process/http_stream.py` with `read_response_head(reader) -> tuple[int, dict[str, str]]` and `iter_body_lines(reader, *, chunked) -> AsyncIterator[bytes]`, Task 3 Step 1 is a no-op.
3. **Name the OOM text once.** Define `oom_error(memory_mb: int) -> str` in `executor.py` rather than inline in `DockerExecutor.status`, so both backends word an OOM kill identically.
4. **Do not change:** `cancel()` = "stop, keep for logs" (K-5 honours it, K5-R5); `submitted_phase` default `pending_capacity`; the credential requeue firing only on `pending_capacity` (K-5 reports a pod waiting for a node as `pending_capacity`, K5-R9, so the rule covers node provisioning too).

## Rulings

Each is a place where the spec, the K-4 plan, the issue and the code disagree or are silent; decided here, with the cost if wrong.

- **K5-R1 · No `kubernetes` client library; stdlib HTTP.** The executor makes nine REST calls and two watches, all on JSON it treats as dicts anyway (the official client has no typed Kueue models). In-cluster auth is "read the projected token file per request, trust the mounted CA" — re-reading per request is what makes token rotation (bound tokens rotate about hourly) a non-issue; an open watch is not re-authenticated, and every watch ends within `WATCH_TIMEOUT_SECONDS` and reconnects with the current token. The watch stream reuses K-4's chunked line reader over an `ssl` asyncio stream. *Why:* "no new dependencies without clear need" and the Docker executor's documented reason (a client library widens the dependency surface of a security-critical component); the official client pulls in `google-auth`, `requests-oauthlib`, `websocket-client`, `pyyaml`, `durationpy`, is sync-only and leaves 410 handling to the caller regardless. *Cost if wrong:* we own TLS, token and watch edge cases; mitigated by K-6's kind leg (the first real API server and real TLS) and a narrow client with its own tests. No kubeconfig parsing: out of cluster (K-6's CI) the settings name a URL, a token file and a CA file.
- **K5-R2 · One Job per SUBMISSION, not per run: `stac-run-{run_id}-{hex6}`.** The spec names the Job `stac-run-{run_id}`. But a run is resubmitted after a `lost` or credential requeue (K-4 rows 3 and 5) and a Kubernetes delete is asynchronous — a foreground-deleted Job lingers for its pod's grace period — so a fixed name would answer `409 AlreadyExists` to the immediate resubmission. *Cost:* the name no longer equals the run id; the run id is always on the `stac-higher.run-id` label and parseable from the name (`parse_job_name`). 52 characters: the pod name (`+6`) and Kueue's Workload name (`job-…-xxxxx`) stay within 63.
- **K5-R3 · Job first, then the Secret, owned by the Job.** An ownerReference needs the owner's uid, which exists only after the Job is created; creating the Secret first and patching its owner afterwards would need `patch secrets`. Kueue suspends a queue-labelled Job at admission, so the Secret is in place long before a pod exists; if a pod ever races it (no Kueue installed), the kubelet retries `CreateContainerConfigError` until the Secret appears (bounded by K5-R7's grace). A failed Secret create deletes the Job and raises `ExecutorUnavailable`. A worker crash between the two leaves a Secret-less Job on an unsubmitted row, which K-4's stall sweep (I9) requeues and the reaper then deletes. *Cost:* one unlucky pod start may be delayed by kubelet retries.
- **K5-R4 · The timeout is the POD's `activeDeadlineSeconds`; the Job's is `timeout + 600`.** A Job past its own deadline is failed by the Job controller, which **deletes** its running pods — and a deleted pod's log is gone, so a timed-out run (the log an operator most wants) would always record an empty log. The kubelet enforces a pod deadline by killing the containers and keeping the pod (`status.reason: DeadlineExceeded`), so the log survives. The pod's clock starts when the kubelet accepts it (after Kueue admission and scheduling), the Job's when Kueue unsuspends it; the Job deadline is the backstop for a pod that never starts. The issue's "`activeDeadlineSeconds = timeout`" is honoured on the pod. *Cost:* a pod that never schedules holds its Kueue quota up to `timeout + 600` s unless Kueue's `waitForPodsReady` (K-6) evicts it first.
- **K5-R5 · `cancel()` stops a started pod by setting its `activeDeadlineSeconds` to 1; a Job with no started pod is deleted (foreground).** Kubernetes cannot stop a container and keep its log except through a deadline: deleting the Job or the pod removes the log, and K-4's cancel row reads logs *after* `cancel` (C5/C6). Pod `spec.activeDeadlineSeconds` is one of the few mutable pod fields (it may be set or lowered). The kubelet then sends SIGTERM, waits `terminationGracePeriodSeconds` (10 s, Docker's stop grace) and kills; the pod stays `Failed` until reap. The spec's "foreground delete after logs" is realised by K-4's order: `cancel` (stop) → logs → `reap` (foreground delete). *Cost:* needs `patch pods`; the stop is asynchronous, so the log K-4 collects right after `cancel` may miss the last seconds of output. *Unverified until K-6:* that the kubelet keeps the dead container's log after a deadline kill — if not, fall back to asking K-4 to collect logs before `cancel` in row 1 (a reorder inside `collecting`).
- **K5-R6 · Kueue preemption is Kueue's requeue; any other pod disruption is `lost`.** Kueue preempts by suspending the Job: its pod is deleted, the Workload goes back to the queue (`Evicted`, reason `Preempted`) and is readmitted later, same Job, fresh pod. `status()` reports that as `pending_capacity` with detail `"preempted: …"` — the run stays submitted, **spends no attempt**, keeps its Kueue position, and the preempted execution's partial log is lost (the pod is gone). A pod the cluster disrupted any other way (node failure, spot reclaim, kubelet eviction, PodGC — pod condition `DisruptionTarget` or `status.reason == "Evicted"`) fails the Job (`backoffLimit: 0`); `status()` reports `lost` with the disruption in `detail`, and K-4 requeues it without spending an attempt (row 5). Run pods carry `karpenter.sh/do-not-disrupt: "true"` so Auto Mode's consolidation never chooses them. *Why not `podFailurePolicy: Ignore`:* it would re-run user code inside the same Job on a shrinking deadline with the same credentials; the platform owns retry. *Cost:* a node that keeps failing loops a run at 60 s spacing (K-4 R15); a preempted run's credentials may expire while it re-waits, which K-4's row 3 catches.
- **K5-R7 · Pull and config failures surface in `status()`, after a grace.** Kubernetes pulls after submit, so `ImagePullFailed` cannot be raised by `submit`. A container waiting with `InvalidImageName` or `ErrImageNeverPull` is `exited` at once; with `ErrImagePull`, `ImagePullBackOff`, `CreateContainerConfigError` or `CreateContainerError` it is `starting` (detail = the reason and message) until `START_FAILURE_GRACE_SECONDS` (300) after the pod's `status.startTime` (or creation), then `exited` with `ExitStatus(-1, error="… could not start (<reason>): <message>")`, which spends an attempt like Docker's `ImagePullFailed`. No pod watch: these are found by the run tick (≤ 60 s after the grace). *Cost:* a bad image costs ~6 minutes and an attempt per try instead of failing at submit.
- **K5-R8 · A Workload Kueue deactivated is `exited` and spends an attempt.** After `waitForPodsReady`'s requeue budget (K-6's config) Kueue sets `spec.active: false` and the Job stays suspended forever. `status()` reports `exited`, `ExitStatus(-1, error="Kueue deactivated the run: <message>")`. *Why:* a run that can never get a node should reach `dead` and raise `process_failed`, not requeue silently. *Cost:* a transient capacity drought costs attempts.
- **K5-R9 · A pod waiting for a node is `pending_capacity`, not `starting`.** Spec §2's gate: the row shows *Waiting for capacity* while Karpenter provisions a node. Pod condition `PodScheduled=False`, reason `Unschedulable` ⇒ `pending_capacity`, detail `"waiting for a node: <scheduler message>"`. Consequence, wanted: K-4's credential-expiry requeue (row 3) covers node provisioning too. *Cost:* an admitted run can be requeued (losing its admission) while its node is coming up, when its credentials run short.
- **K5-R10 · RBAC is exactly what the code calls — narrower than spec §6.3 in four places, wider in one.** `batch/jobs`: create, get, list, watch, delete. `pods`: list, patch (no get, no watch — status lists pods by the job-name label; there is no pod watch). `pods/log`: get. `secrets`: create, delete (no `get`: the pipeline can never read a run's Secret back). `kueue.x-k8s.io/workloads`: list, watch (no `get`: found by label). No `localqueues/pendingworkloads` (queue position is deferred — the visibility API is optional in Kueue and nothing reads it yet). *Wider:* `patch pods` (K5-R5). *Cost:* adding queue position later is a one-rule change plus code. The fake API server enforces this Role, and a test fails if any rule goes unused.
- **K5-R11 · Image scans run as Kubernetes Jobs through the same executor.** I-169: on EKS the seeds' scans "need the Kubernetes executor". A scan Job carries `stac-higher.run-kind: image_scan` (what R-8's scan NetworkPolicy, 443 egress, selects on), the default queue and priority `triggered`, `cpu = spec.cpu` (1.0), no profile. It still blocks in a thread through K-4's `run_to_exit` (C12), whose own clock now also counts Kueue wait and scheduling against the scan's policy timeout. *Cost:* an admission scan waits behind a queue of triggered runs and can time out there; I-124's follow-up (scans on the reconcile path) fixes both.
- **K5-R12 · The registry mirror map is defined and applied here; R-8 wires it into the chart and tests the cache.** `PROCESS_K8S_REGISTRY_MIRRORS` rewrites the registry host of every image the executor runs (platform runtime, scanner, user images) at Job creation, keeping path, tag and digest; an unmapped host, or a reference with no host component, is unchanged. **An image that comes with a registry credential is not rewritten:** the pull-through cache authenticates upstream with the deployment's own credential, which cannot see a group's private image, so it is pulled from upstream with a per-run `kubernetes.io/dockerconfigjson` Secret (`{job}-pull`, owned by the Job like the env Secret). Docker Hub needs no special case: stored references are already normalised to `docker.io/library/…` (`app/src/lib/images/normalize.ts`). *Cost:* a private user image in an egress-restricted cluster cannot be pulled (consistent with the deployment spec's "private ECR images not supported"); the issue's "one Job + one Secret per run" becomes "+ a pull Secret" for private images.
- **K5-R13 · Read-only root everywhere; memory-backed scratch.** `/tmp` is an `emptyDir {medium: Memory, sizeLimit: memory_mb Mi}` on every run, as Docker's user-image tmpfs is; a memory-backed volume counts against the container's memory limit, so filling it is an OOM kill (the run's fault, attempt spent), never a node eviction that K5-R6 would requeue forever. The platform images' `WORKDIR` (`/home/runner`, `/home/scanner`) gets the same; user images get `/tmp` and `HOME=/tmp` (Docker parity — their own `WORKDIR` is read-only on Kubernetes, unlike Docker, which keeps the root writable). The scanner's `grype db update` fails on the read-only `/opt/grype-db` and the scanner already falls back to its baked database (`scan.py`, "scanning with the baked database"); #68 owns the scanner pod's update path. *Cost:* a user image that writes into its own `WORKDIR` works on Docker and fails on Kubernetes — documented in `docs/processes.md`.
- **K5-R14 · Every run pod is uid/gid 10001, `restricted`-compliant, with no token and no identity.** Docker forces uid 10001 only for user images; the platform runtime and scanner images already run as 10001, so forcing it everywhere changes nothing for them and lets the namespace enforce Pod Security `restricted` as a backstop. Pods run under a dedicated `stac-higher-run` ServiceAccount with `automountServiceAccountToken: false` that must never get a Pod Identity association or IRSA annotation (the R-2/R-4 note on #144). `enableServiceLinks: false` keeps the namespace's service variables out of the run's environment. *Residual:* the kubelet still injects `KUBERNETES_SERVICE_HOST/PORT` — the API address, without a token; recorded in ISSUES.
- **K5-R15 · `RunSpec.network` is not read on Kubernetes.** The Docker network name has no Kubernetes meaning; isolation is R-8's NetworkPolicy, selected by `stac-higher.run-kind` (process runs: DNS + the S3 prefix list; scans: 443). Levels above `isolated` stay refused by `check_network_cap`. *Cost:* until R-8 ships its policies, run pods have open egress — R-8 is a hard prerequisite of any cluster deployment (I-160's text says so; Task 11 restates it).
- **K5-R16 · No migration.** K-5 writes only `executor_backend`, `executor_handle`, `phase`, `phase_detail`, `submitted_at` (029) and `credentials_expire_at` (031). K-4's R5 (`started_at` means "claimed") stands: on Kubernetes a run's UI duration and `PROCESS_RUN_SECONDS` include its queue wait. A `running_at` column is a follow-up, recorded in ISSUES, if the lead wants run time split from wait time.
- **K5-R17 · `/health`'s `executor` key is informational.** `{name, reachable, error}`; an unreachable executor never turns `/health` into a 503 (the readiness probe would otherwise pull a pipeline whose ingest is fine — the `image_policy` precedent). `Executor.probe()` is a concrete method on the ABC (default: `list_launched()`; Kubernetes: one `limit=1` Job list, which proves the Role too), run in a thread with a 5 s budget and cached for `EXECUTOR_PROBE_TTL_SECONDS` (the probe runs every 10 s).
- **K5-R18 · The factory builds an executor per use; only the setting fails startup.** `build_executor(settings)` is called where each tick or builder needs one (as `DockerExecutor(...)` is today) and does no I/O. An unknown `PROCESS_EXECUTOR` fails `Settings.from_env` (a misspelt backend must not run silently on Docker); a missing token or unreachable API fails the tick that needed it and shows in `/health`, never the whole worker.
- **K5-R19 · K-3's local capacity applies only to a backend that enforces it.** `Executor.enforces_local_capacity: ClassVar[bool] = False`; `DockerExecutor` and `MemoryExecutor` set it `True`. On Kubernetes capacity is Kueue's quota; a profile file that also carries a `docker.capacity` must not double-gate.
- **K5-R20 · A create the API server refuses as invalid kills the run; anything else requeues.** `422` (field validation, a ValidatingAdmissionPolicy, Kueue's webhook — e.g. an unknown WorkloadPriorityClass) on the Job or Secret create raises `RunRejected` (a new `HardwareUnavailable` subclass, so K-4's mapping makes the run `dead` naming the message); a Secret over the 1 MiB cap is `RunRejected("the run's environment is larger than a Kubernetes Secret allows (1 MiB)")`. `403`, `404` (namespace), `409`, `5xx` and transport errors are `ExecutorUnavailable` (requeue, no attempt). *Cost:* a deployment misconfiguration that answers 422 kills runs loudly (with `process_failed`) instead of parking them.
- **K5-R21 · Two watches, no pod watch; Workloads map to runs by Job name.** Kueue's Workloads do not carry the Job's labels (`labelKeysToCopy` is Kueue-wide config we do not own), so a Workload event's run id is parsed from its Job owner's name (`parse_job_name`). Each watch starts from a `limit=1` LIST's `resourceVersion` (no synthetic ADDED flood — the RESYNC already sweeps the ledger), sends `allowWatchBookmarks=true&timeoutSeconds=300`, resumes from the last seen or bookmarked `resourceVersion` when the server closes it normally, and **raises** on `410 Gone` (an `ERROR` event or HTTP 410), on a transport error or after `WATCH_IDLE_SECONDS` of silence, so K-4's `run_watcher` reconnects and RESYNCs. Pod-level changes that Job status does not reflect (pull errors) are caught by the tick (K5-R7); `starting → running` is a Job `status.ready` change, so it is pushed.
- **K5-R22 · The reaper's age ceiling counts the queue wait.** `reap_max_age_seconds(profiles) = MAX_TIMEOUT_SECONDS + JOB_DEADLINE_SLACK_SECONDS + max(max_queue_wait_seconds) + 3600`, falling back to today's constant when the profile file cannot be read. *Why:* a Job pending in Kueue is old by creation time but alive (K-4 residual (e)).
- **K5-R23 · The chart gets K-5's two settings; R-8 gets the objects.** K-5 adds `PROCESS_K8S_NAMESPACE` (from `processRuns.namespace`) and `PROCESS_K8S_REGISTRY_MIRRORS` (from `registry.mirrorPrefixes`, `ghcr.io` overridden by `global.imageRegistry` exactly as the chart's `mirrorRef` helper does) to the pipeline ConfigMap, and the app's hardware-profiles route reads `PROCESS_EXECUTOR` (already in the shared ConfigMap) instead of its constant. R-8 renders the namespace, run ServiceAccount, Role and RoleBinding from `infra/kubernetes/` (copied into the chart with a drift test — Helm cannot read files outside the chart), the NetworkPolicies, LocalQueue and cluster objects. K-6 applies `infra/kubernetes/` to kind as-is.
- **K5-R24 · GPU mapping is built and unit-tested; GPU runs are not.** `backend.kubernetes.extended_resources` (`{"nvidia.com/gpu": "gpu_count"}`) puts `gpu_count` on requests and limits; `gpu_count > 0` with no such entry is `HardwareUnavailable`, Docker's rule. Profile `image` (the CUDA base) is still not consumed (K-7/K-8); `runtime_class` passes through to `runtimeClassName`.
- **K5-R25 · Switching `PROCESS_EXECUTOR` with runs in flight strands them.** K-4's reconciler only reconciles rows of its own backend (C7/C11). Documented as "drain before switching" (`docs/processes.md`, the chart README); not built.
- **K5-R26 · Logs: head, capped, one stream, current file.** `pods/log?container=run&limitBytes=<max_bytes>&timestamps=false` — combined stdout/stderr, the first `max_bytes`, as Docker returns. The kubelet rotates container logs (10 MiB default) and `pods/log` serves only the current file, so a run that prints more than that loses its head on Kubernetes; recorded beside I-94. No pod (gone, never created) or a container that never started ⇒ `b""` and a WARNING, never a raise.

## Review Focus

- **A run that hits its timeout** must end `failed`/`dead` with `timed_out` **and its log** — the pod deadline keeps the pod (K5-R4): Task 6 `test_a_pod_past_its_deadline_is_timed_out_with_its_exit_code` and Task 5 `test_logs_of_a_failed_pod_are_served`.
- **Cancelling a running run** must stop it, keep its log for K-4's collection, and then delete Job, Secret and pod: Task 5 `test_cancel_of_a_running_pod_sets_its_deadline_and_keeps_it` + `test_reap_deletes_the_job_foreground_and_its_secrets`; the real kubelet behaviour is K-6's first check (Task 11).
- **The environment leaking anywhere but the Secret** (Job spec, an exception, a log line, a repr): Task 4 `test_the_job_carries_no_environment`, Task 5 `test_secret_values_never_reach_errors_or_logs`.
- **A watch that expires or drops** (410, the server's `timeoutSeconds`, a dead TCP connection): no exit missed — Task 7 `test_a_410_ends_the_stream_so_the_watcher_resyncs`, `test_a_normal_close_resumes_from_the_last_bookmark`, `test_silence_past_the_idle_timeout_raises`.
- **Kueue preemption vs. a node failure**: a preempted run must stay submitted as `pending_capacity` (no attempt), a disrupted pod must be `lost` (requeue, no attempt), and neither may read as an ordinary failure — Task 6 `test_a_preempted_job_is_pending_capacity_not_lost` and `test_a_disrupted_pod_is_lost_with_its_reason`.

---

## Tasks

### Task 0: Pre-flight

- [ ] `gh pr list --state merged --search "K-4 in:title"` shows K-4 (#12) merged (and therefore K-3, #11). If not: STOP; Tasks 1–4 do not depend on K-4's code and may proceed only with the lead's say-so.
- [ ] `git fetch origin && git rebase origin/main` in the worktree (K-3 and the K-4 plan commit drop out once merged). `npm install` at the root, `uv sync` in `services/pipeline/`; all gates green before any change.
- [ ] Walk the "What K-5 relies on from K-4" table, C1–C17, against the merged code (`grep -n` each name in `process/executor.py`, `process/reconcile.py`, `process/watcher.py`, `process/launch.py`, `process/runner.py`, `jobs/process.py`, `process/docker_executor.py`, `process/memory_executor.py`, `process/credentials.py`, `process/reaper.py`). For every difference, note it in the PR body and adapt the task that consumes it before starting that task. Check the three "Asks of K-4": if row 5 still writes a fixed error, Task 6 Step 5 makes the change; if `process/http_stream.py` does not exist, Task 3 Step 1 creates it; if `oom_error` does not exist, Task 6 defines it in `executor.py` and points Docker at it.
- [ ] Confirm the chart pieces this plan edits exist on `main` unchanged: `infra/helm/stac-higher/templates/config-env.yaml` sets `PROCESS_EXECUTOR` to `kubernetes` in the shared map; `values.yaml` has `processRuns.namespace`, `registry.mirrorPrefixes`, `global.imageRegistry`; `templates/_helpers.tpl` defines `stac-higher.mirrorRef`; `app/src/pages/api/processes/hardware-profiles.ts` still has `const EXECUTOR_BACKEND = "docker"`. If R-8 (#144) has merged first and already added Role/RoleBinding templates, Task 9's drift test targets them instead of leaving a note for R-8.
- [ ] Kueue API version: read Kueue v0.19.2's release notes / CRDs for the `Workload` versions it serves. Set `KUEUE_API_VERSION` (Task 4) to the storage version (`v1beta2` expected); if `v1beta2` renamed any field this plan reads (`metadata.labels["kueue.x-k8s.io/job-uid"]`, `spec.active`, `status.conditions[].type` `QuotaReserved` / `Admitted` / `Evicted`, `.reason`, `.message`), adapt Task 6's table and say so in the PR.
- [ ] Migrations: `grep -n 'name: "03' app/src/lib/db/migrate.ts` — K-5 adds none (K5-R16); nothing to reserve. Assign yourself: `gh issue edit 13 --add-assignee @me --remove-label blocked`.

### Task 1: The executor vocabulary and the settings

**Files:**
- Create: `tests/contract-fixtures/process-executor.json`
- Modify: `tests/contract-fixtures/README.md` (one table row)
- Modify: `services/pipeline/src/pipeline/config.py` (constants, `Settings` fields, parsers in `from_env`)
- Test: `services/pipeline/tests/test_config.py` (append), `services/pipeline/tests/test_contract_fixtures.py` (append)

**Interfaces:**
- Produces:
  ```json
  { "backends": ["docker", "kubernetes"], "default": "docker" }
  ```
  ```python
  # config.py
  PROCESS_EXECUTORS = ("docker", "kubernetes")
  DEFAULT_PROCESS_EXECUTOR = "docker"
  DEFAULT_PROCESS_K8S_NAMESPACE = "stac-higher-runs"
  DEFAULT_PROCESS_K8S_TOKEN_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/token"
  DEFAULT_PROCESS_K8S_CA_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
  DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT = "stac-higher-run"
  DEFAULT_PROCESS_K8S_DEFAULT_QUEUE = "process-runs"

  class Settings:                                   # gains
      process_executor: str = DEFAULT_PROCESS_EXECUTOR
      process_k8s_namespace: str = DEFAULT_PROCESS_K8S_NAMESPACE
      process_k8s_api_url: str | None = None        # resolved in from_env; None = not in a cluster
      process_k8s_token_file: str = DEFAULT_PROCESS_K8S_TOKEN_FILE
      process_k8s_ca_file: str = DEFAULT_PROCESS_K8S_CA_FILE
      process_k8s_run_service_account: str = DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT
      process_k8s_default_queue: str = DEFAULT_PROCESS_K8S_DEFAULT_QUEUE
      process_k8s_registry_mirrors: Mapping[str, str] = field(default_factory=dict)

  def _parse_process_executor(value: str | None) -> str
  def _parse_k8s_api_url(env: Mapping[str, str]) -> str | None
  def _parse_registry_mirrors(value: str | None) -> dict[str, str]
  ```

- [ ] **Step 1: The fixture.** Write the JSON above with a `$comment` naming K-5, spec §3.3/§6, its readers (`config.PROCESS_EXECUTORS`, Task 10's `app/src/lib/processes/executor.ts`) and that the value is what `process_runs.executor_backend` holds. Add the README row.

- [ ] **Step 2: Failing tests.**
  - `test_contract_fixtures.py`: `test_process_executor_vocabulary_matches_golden` — `PROCESS_EXECUTORS == tuple(fixture["backends"])`, `DEFAULT_PROCESS_EXECUTOR == fixture["default"]`.
  - `test_config.py`:
    - `test_process_executor_defaults_to_docker` and `test_process_executor_is_case_and_space_tolerant` (`" Kubernetes "` → `"kubernetes"`);
    - `test_an_unknown_process_executor_fails_startup` — `PROCESS_EXECUTOR=ecs` raises `ValueError` naming `PROCESS_EXECUTOR` and both allowed values;
    - `test_the_api_url_comes_from_the_in_cluster_variables` — `KUBERNETES_SERVICE_HOST=10.0.0.1`, `KUBERNETES_SERVICE_PORT=443` ⇒ `"https://10.0.0.1:443"`; `fd00::1` ⇒ `"https://[fd00::1]:443"`; neither set ⇒ `None`; `PROCESS_K8S_API_URL` wins over both;
    - `test_plain_http_is_refused_except_to_loopback` — `http://127.0.0.1:8001` and `http://localhost:8001` accepted; `http://10.0.0.1` raises `ValueError`; `ftp://…` raises;
    - `test_registry_mirrors_parse` — `'{"ghcr.io": "123.dkr.ecr.us-east-1.amazonaws.com/ghcr/"}'` ⇒ the prefix without its trailing slash; empty / unset ⇒ `{}`; not an object, a non-string value, a key that is not a registry host (`IMAGE_HOST_RE` from `pipeline.images.reference` — no scheme, no path), or a value with a scheme (`https://…`) each raise `ValueError` naming `PROCESS_K8S_REGISTRY_MIRRORS`;
    - `test_k8s_settings_default` — namespace, token file, CA file, run service account and default queue equal the constants.

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_config.py tests/test_contract_fixtures.py -q`.

- [ ] **Step 4: Implement** the constants, fields and the three parsers, called from `from_env` beside `_parse_network_max`. `_parse_k8s_api_url` reads `PROCESS_K8S_API_URL`, else builds from `KUBERNETES_SERVICE_HOST`/`KUBERNETES_SERVICE_PORT` (bracket a host containing `:`), then validates with `urllib.parse.urlparse`: `https` always; `http` only when the hostname is `127.0.0.1`, `::1` or `localhost` (the unit tests' fake API server; never a real cluster). The mirrors field is stored as a plain `dict` (the frozen dataclass is never hashed). Field comments name K-5 and the spec section, the house style of the neighbouring fields.

- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-5: PROCESS_EXECUTOR and the Kubernetes executor settings; process-executor fixture"
  ```

### Task 2: The runs-namespace RBAC and a fake API server that enforces it

The fake is the test bed for Tasks 3–9; building it against the real Role first means every later test also proves the Role is sufficient.

**Files:**
- Create: `infra/kubernetes/namespace.yaml` (Namespace `stac-higher-runs`, labels `pod-security.kubernetes.io/enforce: restricted`, `pod-security.kubernetes.io/enforce-version: latest`)
- Create: `infra/kubernetes/run-serviceaccount.yaml` (ServiceAccount `stac-higher-run`, `automountServiceAccountToken: false`, a comment: never give it a Pod Identity association or an IRSA annotation)
- Create: `infra/kubernetes/pipeline-role.yaml` (Role `stac-higher-pipeline` in `stac-higher-runs`)
- Create: `infra/kubernetes/pipeline-rolebinding.yaml` (RoleBinding `stac-higher-pipeline` → the Role, subject ServiceAccount `stac-higher-pipeline` in namespace `stac-higher`: the chart's `identity.serviceAccounts.pipeline` and `identity.namespace` defaults)
- Create: `services/pipeline/tests/_fake_kube.py`
- Test: `services/pipeline/tests/test_fake_kube.py` (new)

**Interfaces:**
- Produces the Role's rules — exactly these, in this order (K5-R10):
  ```yaml
  rules:
    - apiGroups: ["batch"]
      resources: ["jobs"]
      verbs: ["create", "get", "list", "watch", "delete"]
    - apiGroups: [""]
      resources: ["pods"]
      verbs: ["list", "patch"]
    - apiGroups: [""]
      resources: ["pods/log"]
      verbs: ["get"]
    - apiGroups: [""]
      resources: ["secrets"]
      verbs: ["create", "delete"]
    - apiGroups: ["kueue.x-k8s.io"]
      resources: ["workloads"]
      verbs: ["list", "watch"]
  ```
  Each manifest's header comment names K-5, spec §6.3, K5-R10 and that R-8 renders the same objects in the chart (with a drift test) while K-6 applies this directory to kind as-is.
- Produces (tests only):
  ```python
  # tests/_fake_kube.py
  ROLE_PATH = <repo>/infra/kubernetes/pipeline-role.yaml
  @dataclass(frozen=True)
  class Call: method: str; path: str; query: dict[str, str]; verb: str; group: str; resource: str; body: Any

  class FakeKube:                      # a context manager; a ThreadingHTTPServer on 127.0.0.1:0
      url: str                         # "http://127.0.0.1:<port>"
      namespace: str                   # "stac-higher-runs"
      token: str                       # requests must send "Authorization: Bearer <token>" (else 401)
      calls: list[Call]
      used: set[tuple[str, str, str]]  # (group, resource, verb) actually exercised — Task 9 reads it
      kueue: bool = True               # emulate Kueue's webhook: suspend a queue-labelled Job + create its Workload
      def objects(self, group: str, resource: str) -> dict[str, dict]
      # controller-side helpers: each bumps resourceVersion and emits a watch event
      def set_job_status(self, name, *, suspend=None, active=None, ready=None, conditions=None) -> None
      def add_pod(self, job: str, *, phase: str, state: dict | None = None, conditions=(),
                  reason: str | None = None, message: str | None = None,
                  start_time: dt.datetime | None = None, log: bytes = b"") -> str
      def set_workload(self, job: str, *, conditions: list[dict], active: bool = True) -> str
      def fail_next(self, method: str, resource: str, status: int, *, reason: str = "",
                    message: str = "") -> None
      def compact(self) -> None          # older resourceVersions now answer 410 Gone
      def bookmark(self) -> None         # every open watch receives a BOOKMARK at the current RV
      def close_watches(self) -> None    # end every open watch stream cleanly
  ```

- [ ] **Step 1: Failing self-tests** (`test_fake_kube.py`, plain `urllib`/`asyncio` against the fake so the fake is pinned before the executor relies on it):
  - `test_rbac_is_enforced_from_the_role_file` — `GET …/secrets/x` (a `get` the Role lacks) answers 403 with a `Status` body whose `reason` is `Forbidden`; `POST …/secrets` is not 403.
  - `test_a_missing_or_wrong_token_is_401`.
  - `test_create_get_list_delete_round_trip_with_label_selectors` — `labelSelector=stac-higher.run-id` (existence) and `a=b` (equality) both filter; `limit=1` returns one item plus `metadata.continue` and the list's `metadata.resourceVersion`.
  - `test_deleting_a_job_cascades_to_what_it_owns` — Secrets with an ownerReference to the Job's uid, pods labelled `batch.kubernetes.io/job-name`, and Workloads labelled `kueue.x-k8s.io/job-uid` are gone; `propagationPolicy` is recorded on the call.
  - `test_kueue_emulation_suspends_a_queue_labelled_job_and_creates_its_workload` — the stored Job has `spec.suspend: true`; one Workload with label `kueue.x-k8s.io/job-uid=<uid>`, an ownerReference to the Job, and `QuotaReserved=False` reason `Pending`.
  - `test_pod_patch_accepts_only_a_lower_deadline` — a merge-patch of `spec.activeDeadlineSeconds` to 1 succeeds; patching any other field answers 422 (the real API's pod-update rule).
  - `test_pod_log_honours_limit_bytes`; `test_log_of_a_waiting_container_is_400`.
  - `test_watch_streams_events_after_the_given_resource_version` (chunked; one JSON object per line; ADDED/MODIFIED/DELETED); `test_watch_from_a_compacted_version_gets_an_error_410_event`; `test_bookmarks_and_close_are_delivered`.
  - `test_fail_next_injects_one_error`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_fake_kube.py -q`.

- [ ] **Step 3: Write the manifests** above (plain YAML, one object per file, `kubectl apply -f infra/kubernetes/`-ready).

- [ ] **Step 4: Write the fake.** A `ThreadingHTTPServer` whose handler: routes `/api/v1/namespaces/{ns}/{resource}[/{name}[/{sub}]]`, `/apis/batch/v1/…`, `/apis/kueue.x-k8s.io/{KUEUE_API_VERSION}/…` (the fake defines its own `KUEUE_API_VERSION = "v1beta2"`; Task 4's `test_the_fake_serves_the_executors_kueue_version` pins the two equal); maps method + shape to an RBAC verb (`GET` item → `get`, `GET` collection → `list`, `?watch=1` → `watch`, `POST` → `create`, `DELETE` → `delete`, `PATCH` → `patch`; `pods/log` is resource `pods/log`) and answers 403 unless the Role (parsed with `yaml.safe_load` from `ROLE_PATH`) allows it; records a `Call` and the `used` triple; stores objects in memory with a global integer `resourceVersion`, `uid` (uuid4) and `creationTimestamp`; answers `404 NotFound`, `409 AlreadyExists` and `410 Expired` as Kubernetes `Status` bodies; implements `limit`/`continue` (an opaque offset), equality and existence label selectors, ownerReference/label cascades on Job delete, merge-patch for pods (deadline only), `pods/{name}/log` (`limitBytes`, 400 when the pod has no started container), and watches as a chunked stream fed from an event log (`threading.Condition`), with `ERROR`/410 for a `resourceVersion` older than the last `compact()`, `BOOKMARK` on `bookmark()`, and a clean chunked terminator on `close_watches()` or the request's `timeoutSeconds`. No TLS (K-6 covers it). Keep it one file, no controller loop: tests move state with the helpers.

- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-5: runs-namespace RBAC under infra/kubernetes (exact verbs, PSA restricted, token-less run SA); a fake API server that enforces it"
  ```

### Task 3: The stream reader moves out of Docker; a small Kubernetes API client

**Files:**
- Create (unless K-4 did — Ask 2): `services/pipeline/src/pipeline/process/http_stream.py`
- Modify: `services/pipeline/src/pipeline/process/docker_executor.py` (import the reader from `http_stream`; behaviour unchanged)
- Create: `services/pipeline/src/pipeline/process/kube_api.py`
- Test: `services/pipeline/tests/test_kube_api.py` (new), `tests/test_process_executor.py` (K-4's body-line tests import from the new module)

**Interfaces:**
- Produces:
  ```python
  # http_stream.py — moved verbatim from K-4's docker_executor.py helpers
  async def read_response_head(reader: asyncio.StreamReader) -> tuple[int, dict[str, str]]  # status, lower-cased headers
  async def iter_body_lines(reader: asyncio.StreamReader, *, chunked: bool) -> AsyncIterator[bytes]

  # kube_api.py
  class KubeApiError(ExecutorUnavailable):
      status: int; reason: str        # the Status body's reason ("NotFound", "Invalid", …)
  class KubeNotFound(KubeApiError): ...   # 404
  class KubeGone(KubeApiError): ...       # 410 (HTTP or a watch ERROR event)

  @dataclass(frozen=True)
  class KubeConfig:
      api_url: str
      token_file: Path
      ca_file: Path | None            # None only for a loopback http:// URL
      request_timeout_seconds: int = REQUEST_TIMEOUT_SECONDS   # 10

  class KubeApi:
      def __init__(self, config: KubeConfig) -> None
      @classmethod
      def from_settings(cls, settings: Settings) -> KubeApi    # ExecutorUnavailable when process_k8s_api_url is None
      def get(self, path: str, *, query: Mapping[str, str] | None = None) -> dict
      def list(self, path: str, *, label_selector: str, limit: int = 500) -> tuple[list[dict], str]  # follows `continue`; returns (items, list resourceVersion)
      def create(self, path: str, body: dict, *, redact: bool = False) -> dict
      def patch(self, path: str, body: dict) -> dict          # Content-Type: application/merge-patch+json
      def delete(self, path: str, *, propagation: str | None = None) -> None
      def get_raw(self, path: str, *, query: Mapping[str, str] | None = None) -> bytes
      async def watch(self, path: str, *, query: Mapping[str, str],
                      idle_timeout: float | None = None) -> AsyncIterator[dict]  # one decoded {"type","object"} per line
  ```
  Error messages are `f"kubernetes {method} {path} failed: {status} {reason}: {message[:300]}"`; with `redact=True` the `message` part is dropped (Secrets: the API server's message is never echoed). Paths are API paths (`/apis/batch/v1/namespaces/{ns}/jobs`); the client owns the base URL.

- [ ] **Step 1: The move.** If `process/http_stream.py` does not exist, move K-4's `_iter_body_lines` and the status-line + header parse out of `DockerExecutor.watch` into it under the public names above; `docker_executor.py` imports them. Run K-4's Docker events tests unchanged (only their import line moves): `uv run pytest tests/test_process_executor.py -q` — PASS before and after.

- [ ] **Step 2: Failing tests** (`test_kube_api.py`, against `FakeKube`):
  - `test_requests_carry_the_bearer_token_from_the_file` — and `test_a_rotated_token_is_used_on_the_next_request` (rewrite the file, set `fake.token` to the new value, the next call succeeds without a new `KubeApi`).
  - `test_a_missing_token_file_is_unavailable` — `ExecutorUnavailable` naming the path, not `FileNotFoundError`.
  - `test_error_statuses_map_to_typed_errors` — 404 ⇒ `KubeNotFound`, 410 ⇒ `KubeGone`, 403/409/422/500 ⇒ `KubeApiError` with `.status` and `.reason` from the `Status` body; all are `ExecutorUnavailable` subclasses.
  - `test_a_refused_connection_is_unavailable` (a closed port).
  - `test_a_redacted_create_never_echoes_the_server_message` — `fail_next("POST", "secrets", 422, message="data[AWS_SECRET_ACCESS_KEY]: s3cr3t")` ⇒ the exception text contains `422` and `Invalid` and not `s3cr3t`.
  - `test_list_follows_continue_and_returns_the_resource_version` — three Jobs, `limit=1` ⇒ three items over three calls, the RV of the last page.
  - `test_patch_sends_a_merge_patch`.
  - `test_watch_yields_decoded_events_and_raises_gone_on_an_error_410` — after `fake.compact()`, a watch from the old RV yields nothing and raises `KubeGone`; an HTTP 410 on connect raises `KubeGone` too.
  - `test_watch_raises_unavailable_on_a_non_200`; `test_watch_raises_unavailable_after_idle_timeout_of_silence` (`idle_timeout=0.2`, a watch the fake keeps open with no events).
  - `test_https_uses_the_ca_file` — unit-level: `KubeApi(KubeConfig("https://x:443", token, ca))` builds its `ssl.SSLContext` with `cafile=ca` and `check_hostname` on (assert through an injected `ssl_context_factory` seam, `field(default=ssl.create_default_context, repr=False)`); no network.

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_kube_api.py -q`.

- [ ] **Step 4: Implement.** Sync calls: `urllib.request.build_opener(HTTPSHandler(context=ctx))` (plain `HTTPHandler` for a loopback `http://` URL), `Authorization: Bearer <token>` read from `token_file` on every request (`.strip()`), `Accept: application/json`, per-request timeout. Map `HTTPError` by parsing its body as a `Status` (fall back to the HTTP reason); `URLError`/`OSError`/`TimeoutError` ⇒ `ExecutorUnavailable`. `watch`: parse the URL, `asyncio.open_connection(host, port, ssl=ctx, server_hostname=host)` (no `ssl` for loopback http), send `GET {path}?{urlencode(query)} HTTP/1.1` with `Host`, the bearer header, `Accept: application/json`, `Connection: close`; `read_response_head` (410 ⇒ `KubeGone`, other non-200 ⇒ `KubeApiError`), then `iter_body_lines` → `json.loads` per line, each line awaited under `asyncio.wait_for(…, idle_timeout)` when one is given (a timeout ⇒ `ExecutorUnavailable("kubernetes watch … idle")`); an `ERROR` event whose object is a `Status` with `code == 410` raises `KubeGone`, any other `ERROR` raises `KubeApiError`; close the writer in `finally`. Module docstring: K5-R1 in three sentences.

- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-5: kube_api — stdlib Kubernetes client (token re-read per request, typed Status errors, merge-patch, chunked watch); the HTTP stream reader shared with Docker"
  ```

### Task 4: The profile's Kubernetes block, the mirror map and the manifests (pure)

Everything a Job is made of, as pure functions of the `RunSpec`, the profile and the settings — no I/O, so the security-relevant shape is pinned by plain dict assertions.

**Files:**
- Modify: `services/pipeline/src/pipeline/process/executor.py` (move `RUN_ID_LABEL`, `PROCESS_ID_LABEL`, `RUN_KIND_LABEL`, `IMAGE_ID_LABEL`, `RUN_UID = 10001` here; add `RunRejected`, `enforces_local_capacity`, `probe()` — the latter two used in Task 8)
- Modify: `services/pipeline/src/pipeline/process/docker_executor.py` (import the label constants from `executor.py`; `USER_IMAGE_USER = f"{RUN_UID}:{RUN_UID}"`; set `enforces_local_capacity = True`)
- Modify: `services/pipeline/src/pipeline/process/memory_executor.py` (`enforces_local_capacity = True`)
- Modify: `services/pipeline/src/pipeline/process/hardware.py` (`KubernetesBackend`, `kubernetes_backend`)
- Create: `services/pipeline/src/pipeline/process/kubernetes_manifests.py`
- Test: `services/pipeline/tests/test_kubernetes_manifests.py` (new), `services/pipeline/tests/test_process_hardware.py` (append — use the existing K-1/K-3 hardware test file; `grep -ln docker_backend tests/`)

**Interfaces:**
- Produces:
  ```python
  # executor.py
  class RunRejected(HardwareUnavailable):
      """The backend refused the run's definition (K5-R20): configuration, the run dies."""
  class Executor(abc.ABC):                       # gains (K5-R17, K5-R19)
      enforces_local_capacity: ClassVar[bool] = False
      def probe(self) -> None: ...                # default: self.list_launched(); raises ExecutorUnavailable

  # hardware.py
  @dataclass(frozen=True)
  class KubernetesBackend:
      queue: str | None = None
      node_selector: Mapping[str, str] = field(default_factory=dict)
      tolerations: tuple[Mapping[str, Any], ...] = ()
      runtime_class: str | None = None
      extended_resources: Mapping[str, str] = field(default_factory=dict)   # resource name -> "gpu_count"
  def kubernetes_backend(profile: HardwareProfile) -> KubernetesBackend

  # kubernetes_manifests.py
  QUEUE_LABEL = "kueue.x-k8s.io/queue-name"
  PRIORITY_LABEL = "kueue.x-k8s.io/priority-class"
  KUEUE_JOB_UID_LABEL = "kueue.x-k8s.io/job-uid"
  POD_JOB_LABEL = "batch.kubernetes.io/job-name"
  DO_NOT_DISRUPT = "karpenter.sh/do-not-disrupt"
  KUEUE_API_VERSION = "v1beta2"
  CONTAINER = "run"
  JOB_DEADLINE_SLACK_SECONDS = 600
  TERMINATION_GRACE_SECONDS = 10
  PLATFORM_WORKDIRS = {"process": "/home/runner", "image_scan": "/home/scanner"}
  NAME_PREFIX = {"process": "stac-run-", "image_scan": "stac-scan-"}

  @dataclass(frozen=True)
  class JobContext:                    # the settings a manifest needs
      namespace: str
      default_queue: str
      run_service_account: str
      registry_mirrors: Mapping[str, str]

  def job_name(kind: str, run_id: str, suffix: str) -> str
  def parse_job_name(name: str) -> tuple[str, str] | None             # (kind, run_id)
  def rewrite_image(image: str, mirrors: Mapping[str, str]) -> str
  def run_labels(spec: RunSpec) -> dict[str, str]
  def build_job(spec: RunSpec, *, name: str, ctx: JobContext, pull_secret: str | None) -> dict
  def build_env_secret(spec: RunSpec, *, name: str, owner_uid: str, ctx: JobContext) -> dict
  def build_pull_secret(spec: RunSpec, *, name: str, owner_uid: str, ctx: JobContext) -> dict | None
  ```

- [ ] **Step 1: Failing tests — the profile block** (`test_process_hardware.py`):
  - `test_an_absent_kubernetes_block_is_the_defaults` (`local.json`'s `standard` ⇒ `KubernetesBackend()`).
  - `test_the_charts_eks_profiles_parse` — read `infra/helm/stac-higher/files/hardware-profiles/eks-cpu.json` from the checkout: both profiles ⇒ `queue == "process-runs"`, empty selector/tolerations/resources, `runtime_class is None`.
  - `test_a_gpu_block_parses` — the spec §3.4 `gpu-l4` block (selector `{"stac-higher.io/flavor": "gpu-l4"}`, toleration `nvidia.com/gpu Exists NoSchedule`, `extended_resources {"nvidia.com/gpu": "gpu_count"}`).
  - `test_a_malformed_kubernetes_block_names_the_profile` — each raises `HardwareProfileError` with `profile 'p': backend.kubernetes…`: block not an object; `queue` not a non-empty string; a selector value that is not a string; a toleration with an unknown key, an `operator` outside `Exists|Equal` or an `effect` outside `""|NoSchedule|PreferNoSchedule|NoExecute`; `runtime_class` not a string/null; an `extended_resources` value other than `"gpu_count"`.

- [ ] **Step 2: Failing tests — names, mirror map, manifests** (`test_kubernetes_manifests.py`; a `RunSpec` builder helper with a profile carrying the gpu-l4 block, `ctx = JobContext("stac-higher-runs", "process-runs", "stac-higher-run", {"ghcr.io": "123.dkr.ecr.us-east-1.amazonaws.com/ghcr", "docker.io": "123.dkr.ecr.us-east-1.amazonaws.com/docker-hub"})`):
  - `test_job_names_fit_and_round_trip` — `job_name("process", RUN, "a1b2c3") == f"stac-run-{RUN}-a1b2c3"`, ≤ 63 chars for a uuid run id; `parse_job_name` returns `("process", RUN)` / `("image_scan", SCAN)`; a stranger's name ⇒ `None`.
  - `test_rewrite_image_swaps_the_host_only` — `ghcr.io/techtraverse/stac-higher-process-runtime@sha256:<64>` ⇒ the mirror prefix + `/techtraverse/…@sha256:<same>`; `docker.io/library/python@sha256:…` ⇒ `…/docker-hub/library/python@sha256:…`; a tag survives; `quay.io/x/y:1` unmapped ⇒ unchanged; `stac-higher-process-runtime:dev` (no host component) ⇒ unchanged; `localhost:5000/x:1` ⇒ unchanged unless `localhost:5000` is a key.
  - `test_the_job_is_kueue_labelled_and_never_retried` — labels include `stac-higher.run-id`, `stac-higher.run-kind`, `stac-higher.process-id`, `QUEUE_LABEL: "gpu-queue"` for a profile whose block names `queue: "gpu-queue"` / `"process-runs"` (`ctx.default_queue`) with no profile or no `queue`, `PRIORITY_LABEL: "interactive"` for an interactive spec; `spec.backoffLimit == 0`, `completions == parallelism == 1`, `activeDeadlineSeconds == timeout + 600`, no `ttlSecondsAfterFinished`, no `suspend`, no `podFailurePolicy`.
  - `test_the_pod_deadline_is_the_run_timeout` — `template.spec.activeDeadlineSeconds == spec.timeout_seconds`, `terminationGracePeriodSeconds == 10`, `restartPolicy == "Never"`.
  - `test_the_pod_is_locked_down` — pod `securityContext` exactly the Global Constraints value; the container's exactly its value; `automountServiceAccountToken is False`, `enableServiceLinks is False`, `serviceAccountName == "stac-higher-run"`, annotation `karpenter.sh/do-not-disrupt: "true"`; no `hostNetwork`/`hostPID`/`hostIPC`/`privileged` anywhere.
  - `test_the_job_carries_no_environment` — `json.dumps(job)` contains none of the spec's env values and no `"env":` key; the container has exactly `envFrom: [{"secretRef": {"name": name}}]`.
  - `test_requests_equal_limits` — cpu 2.5 ⇒ `"2500m"`, memory 16384 ⇒ `"16384Mi"`, `gpu_count` 1 with the gpu block ⇒ `"nvidia.com/gpu": "1"` in both; `gpu_count` 0 ⇒ no GPU key; `gpu_count` 1 with a profile lacking `extended_resources` ⇒ `HardwareUnavailable` naming the profile.
  - `test_placement_comes_from_the_profile` — `nodeSelector`, `tolerations`, `runtimeClassName` copied; absent ⇒ keys absent.
  - `test_scratch_is_memory_backed` — `/tmp` mount backed by `emptyDir {medium: Memory, sizeLimit: "<memory_mb>Mi"}`; a platform process spec also mounts `/home/runner`, a scan `/home/scanner`, a user image (`user_image=True`) only `/tmp`.
  - `test_entrypoint_and_cmd_map_to_command_and_args` — `spec.entrypoint` ⇒ `command`, `spec.cmd` ⇒ `args`; empty ⇒ keys absent.
  - `test_the_image_is_rewritten_unless_it_has_a_credential` — a public user image is mirrored; one with `registry_auth` is not, and the pod gets `imagePullSecrets: [{"name": f"{name}-pull"}]`.
  - `test_a_scan_job` — `kind="image_scan"`: name prefix `stac-scan-`, `stac-higher.image-id` instead of `process-id`, default queue, priority `triggered`.
  - `test_the_env_secret_is_owned_and_immutable` — `type: Opaque`, `immutable: True`, `data` values are base64 of `spec.env` values (round-trip), keys equal `spec.env` keys, labels = `run_labels(spec)`, `ownerReferences == [{"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": UID, "controller": False, "blockOwnerDeletion": False}]`.
  - `test_the_pull_secret_is_a_dockerconfigjson` — `type: kubernetes.io/dockerconfigjson`, `.dockerconfigjson` decodes to `{"auths": {server: {"username", "password", "auth": b64("user:pass")}}}` with `server = registry_auth.server or registry_host(image)`; `None` without `registry_auth`; same ownerReference.
  - `test_building_manifests_logs_nothing` — `caplog` at DEBUG is empty after building all three manifests (the builders never log what they carry).
  - `test_the_fake_serves_the_executors_kueue_version` — `_fake_kube.KUEUE_API_VERSION == kubernetes_manifests.KUEUE_API_VERSION`.

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_kubernetes_manifests.py tests/test_process_hardware.py -q`.

- [ ] **Step 4: Implement.** `kubernetes_backend` is `docker_backend`'s lenient twin (absent/`null` block ⇒ defaults; present-but-wrong ⇒ `HardwareProfileError` naming the profile). `rewrite_image`: the first `/`-component is a registry host only if it contains `.` or `:` or equals `localhost` (Docker's own rule); when it is a key of `mirrors`, return `mirrors[host] + image[len(host):]`. `build_job`'s image is `spec.image` if `spec.registry_auth` else `rewrite_image(spec.image, ctx.registry_mirrors)`. The CPU quantity is `f"{round(spec.cpu * 1000)}m"`. A user image keeps Docker's digest rule: `submit` (Task 5) refuses a `user_image` not pinned by digest before calling these builders, so the builders assume a valid spec. Move the label constants and `RUN_UID` into `executor.py` and re-import them in `docker_executor.py` (every existing import of `docker_executor.RUN_ID_LABEL` etc. keeps working).

- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-5: the Job, Secret and pull-Secret manifests as pure functions; backend.kubernetes profile block; registry mirror rewrite (host only, digest kept)"
  ```

### Task 5: `KubernetesExecutor` — submit, cancel, logs, reap, list_launched, probe

`status()` and `watch()` follow in Tasks 6 and 7; until then they raise `NotImplementedError` so the class is instantiable for these tests (the factory does not select it before Task 8).

**Files:**
- Create: `services/pipeline/src/pipeline/process/kubernetes_executor.py`
- Test: `services/pipeline/tests/test_kubernetes_executor.py` (new; a `fake_kube` fixture yields a started `FakeKube`, an `executor` fixture builds `KubernetesExecutor(api=KubeApi(KubeConfig(fake.url, token_file, None)), …)` with `_suffix=lambda: "a1b2c3"`)

**Interfaces:**
- Consumes: Task 3's `KubeApi`, `KubeNotFound`; Task 4's builders, `JobContext`, `RunRejected`, label constants.
- Produces:
  ```python
  SECRET_MAX_BYTES = 1024 * 1024           # the API server's Secret cap

  @dataclass
  class KubernetesExecutor(Executor):
      api: KubeApi
      namespace: str = DEFAULT_PROCESS_K8S_NAMESPACE
      default_queue: str = DEFAULT_PROCESS_K8S_DEFAULT_QUEUE
      run_service_account: str = DEFAULT_PROCESS_K8S_RUN_SERVICE_ACCOUNT
      registry_mirrors: Mapping[str, str] = field(default_factory=dict)
      name: str = "kubernetes"
      _clock: Callable[[], dt.datetime] = field(default=_utcnow, repr=False)
      _suffix: Callable[[], str] = field(default=lambda: secrets.token_hex(3), repr=False)

      @classmethod
      def from_settings(cls, settings: Settings) -> KubernetesExecutor
      def submit(self, spec: RunSpec) -> RunHandle         # RunHandle(id=<job name>, backend="kubernetes")
      def cancel(self, handle: RunHandle) -> None
      def logs(self, handle: RunHandle, max_bytes: int) -> bytes
      def reap(self, handle: RunHandle) -> None
      def list_launched(self) -> list[LaunchedRun]
      def probe(self) -> None
      def status(self, handle: RunHandle) -> RunState      # Task 6
      async def watch(self) -> AsyncIterator[RunEvent]     # Task 7
  ```

- [ ] **Step 1: Failing tests.**
  - Submit:
    - `test_submit_creates_the_job_then_its_owned_secret` — `fake.calls` are `POST jobs` then `POST secrets`; the stored Secret's ownerReference `uid` equals the stored Job's `uid`; the handle is `RunHandle(f"stac-run-{RUN}-a1b2c3", "kubernetes")`; with `fake.kueue` on, the Job is suspended and has a Workload.
    - `test_submit_with_a_registry_credential_creates_the_pull_secret` — a third call `POST secrets` for `{name}-pull`, type `kubernetes.io/dockerconfigjson`.
    - `test_a_user_image_not_pinned_by_digest_is_refused_before_any_call` — `ImagePullFailed`, `fake.calls == []` (Docker's rule and message).
    - `test_a_gpu_run_without_an_extended_resource_creates_nothing` — `HardwareUnavailable`, no calls.
    - `test_an_oversized_environment_is_rejected_before_any_call` — one 2 MiB env value ⇒ `RunRejected` whose message names the 1 MiB cap and not the value; no calls.
    - `test_a_rejected_job_is_a_dead_run` — `fail_next("POST", "jobs", 422, reason="Invalid", message="spec.template…: Invalid value")` ⇒ `RunRejected` (so `isinstance(err, HardwareUnavailable)`), message names the Job and the server's message.
    - `test_a_forbidden_or_failing_create_is_unavailable` — 403 and 500 on `POST jobs` ⇒ `ExecutorUnavailable`, not `RunRejected`.
    - `test_a_failed_secret_create_removes_the_job` — `fail_next("POST", "secrets", 500)` ⇒ `ExecutorUnavailable`, then no Job (and no Workload) remains; a 422 there ⇒ `RunRejected`, Job removed too.
    - `test_secret_values_never_reach_errors_or_logs` — with env `{"API_TOKEN": "tok-SENTINEL"}`: a 422 on `POST secrets` whose fake message echoes `tok-SENTINEL`, and a 500 on `POST jobs`; neither `str(err)` nor `caplog.text` (DEBUG) contains `SENTINEL`; and on success `json.dumps(fake.objects("batch", "jobs"))` does not contain it.
  - Cancel:
    - `test_cancel_of_a_running_pod_sets_its_deadline_and_keeps_it` — Job admitted (`set_job_status(suspend=False, active=1)`), `add_pod(phase="Running", start_time=…)`: one `PATCH pods/<pod>` with body `{"spec": {"activeDeadlineSeconds": 1}}`; the Job, the pod and the Secret still exist; no `DELETE`.
    - `test_cancel_of_a_never_started_job_deletes_it` — suspended Job (and, separately, an admitted Job whose pod has no `startTime`): `DELETE jobs/<name>` with `propagationPolicy=Foreground`; Job, Secret and Workload gone.
    - `test_cancel_is_idempotent` — a missing Job ⇒ returns quietly after one `GET`; a `Complete` or `Failed` Job ⇒ no `PATCH`/`DELETE`.
    - `test_cancel_raises_when_the_api_is_down` — 500 on the Job `GET` ⇒ `ExecutorUnavailable` (K-4 row 1 then reports `unavailable`).
  - Logs:
    - `test_logs_of_a_failed_pod_are_served` — a `Failed` pod with `log=b"hello\n" * 10`: returns the bytes; the call's query has `container=run`, `limitBytes=<max_bytes>`, `timestamps=false`; `max_bytes=8` ⇒ 8 bytes.
    - `test_logs_pick_the_newest_pod` (two pods by `creationTimestamp`).
    - `test_logs_without_a_pod_or_a_started_container_are_empty` — no pod, and a pod whose container never started (fake answers 400): `b""`, one WARNING `"process run logs unavailable"` with `extra` `run`, never a raise; an API outage likewise `b""` (the verdict is already known — Docker's rule).
  - Reap, list, probe:
    - `test_reap_deletes_the_job_foreground_and_its_secrets` — `DELETE jobs/<name>?propagationPolicy=Foreground`, `DELETE secrets/<name>`, `DELETE secrets/<name>-pull`; nothing of the run remains.
    - `test_reap_is_idempotent_and_never_raises` — twice in a row (404s are success); a 500 ⇒ WARNING `"process run reap failed"`, no raise.
    - `test_list_launched_reads_labels_and_creation_times` — a process Job and a scan Job ⇒ two `LaunchedRun`s with `handle.id` = Job name, `run_id` from the label, `kind` `"process"` / `"image_scan"`, `created_at` = the parsed `creationTimestamp` (UTC); a Job without the run-id label in the namespace is skipped.
    - `test_list_launched_raises_on_any_api_error` — 403 and 500 ⇒ `ExecutorUnavailable`, never `[]`.
    - `test_probe_lists_at_most_one_job` — the call's query has `limit=1` and the run-id label selector; a 403 raises `ExecutorUnavailable`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_kubernetes_executor.py -q`.

- [ ] **Step 3: Implement.**
  - `submit`: (1) a `user_image` must be `reference@digest` (`is_image_reference`/`is_image_digest`, Docker's `ImagePullFailed` text); (2) `name = job_name(spec.kind, spec.run_id, self._suffix())`; build the Job, env Secret and pull Secret **before** any call (`HardwareUnavailable` from the GPU rule surfaces here); refuse when `len(json.dumps(env_secret["data"]))` exceeds `SECRET_MAX_BYTES` (`RunRejected`); (3) `POST` the Job — `KubeApiError` with status 422 ⇒ `RunRejected(f"the cluster rejected run {spec.run_id}'s Job: {err}")`, anything else propagates as `ExecutorUnavailable`; (4) read `metadata.uid` from the response, set it on the Secrets' ownerReference (`build_env_secret(…, owner_uid=uid)`), `POST` them with `redact=True`; on any error delete the Job (`propagation="Foreground"`, errors suppressed and logged at WARNING with `run_id`) and raise `RunRejected("the cluster rejected run …'s environment Secret (422)")` for a 422 or 413, else the redacted `ExecutorUnavailable`. Never log the spec, the env or a manifest.
  - `cancel`: `GET` the Job (`KubeNotFound` ⇒ return); a `Complete`/`Failed` condition ⇒ return; list pods by `batch.kubernetes.io/job-name=<name>`; for each pod with `status.startTime` set and `status.phase` not `Succeeded`/`Failed`, merge-patch `spec.activeDeadlineSeconds = CANCEL_POD_DEADLINE_SECONDS` (a `KubeNotFound` there is fine); if no pod qualified, `DELETE` the Job with `propagation="Foreground"` (`KubeNotFound` ⇒ fine). Docstring: K5-R5, and that the log is read by the caller next and the Job deleted by `reap`.
  - `logs`: list pods by the job-name label, newest `creationTimestamp`; `get_raw(f"…/pods/{pod}/log", query={"container": "run", "limitBytes": str(max_bytes), "timestamps": "false"})[:max_bytes]`; any `ExecutorUnavailable` ⇒ WARNING + `b""`.
  - `reap`: `DELETE` Job (Foreground), env Secret, pull Secret, each tolerating `KubeNotFound`; any other error ⇒ WARNING `"process run reap failed"` (`extra={"run": handle.id, "error": str(err)}`), continue with the next delete, never raise. Explicit Secret deletes are belt and braces over the ownerReference GC.
  - `list_launched`: `api.list(jobs, label_selector=RUN_ID_LABEL)`; `creationTimestamp` parsed with `dt.datetime.fromisoformat` (`Z` → `+00:00`); errors propagate.
  - `probe`: `api.get(jobs, query={"labelSelector": RUN_ID_LABEL, "limit": "1"})`.
  - `from_settings`: `KubeApi.from_settings(settings)` + the five settings. Module docstring: what one run is on Kubernetes (Job + Secret), the K5-R2/R3/R4/R5 decisions in a sentence each, and the ADR 0013 invariants restated for this backend (complete env via the Secret; limits on the pod; reap explicit; list_launched never `[]` on an outage).

- [ ] **Step 4: Run to verify they pass** — `uv run pytest tests/test_kubernetes_executor.py -q`.

- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-5: KubernetesExecutor submit (Job, then its owned env Secret), cancel (pod deadline keeps the log), logs via pods/log, foreground reap, list_launched"
  ```

### Task 6: `status()` — Job, Workload and pod onto K-4's five phases

**Files:**
- Modify: `services/pipeline/src/pipeline/process/kubernetes_executor.py` (`status`, pure helper `_job_state`)
- Modify (only if K-4 lacks them — Asks 1 and 3): `services/pipeline/src/pipeline/process/executor.py` (`oom_error`), `services/pipeline/src/pipeline/process/docker_executor.py` (use it), `services/pipeline/src/pipeline/process/reconcile.py` (row 5 uses `state.detail`)
- Test: `services/pipeline/tests/test_kubernetes_executor_status.py` (new; same fixtures; a fixed `_clock`), `services/pipeline/tests/test_process_reconcile.py` (Ask 1 only)

**Interfaces:**
- Consumes: Task 5's executor; K-4's `RunState`, `ExitStatus` (C2).
- Produces:
  ```python
  START_FAILURE_GRACE_SECONDS = 300
  IMMEDIATE_START_FAILURES = frozenset({"InvalidImageName", "ErrImageNeverPull"})
  GRACED_START_FAILURES = frozenset({"ErrImagePull", "ImagePullBackOff",
                                     "CreateContainerConfigError", "CreateContainerError"})
  DISRUPTION_EXIT = "the run's pod was disrupted"
  def status(self, handle: RunHandle) -> RunState
  def _job_state(job: dict, workload: dict | None, pod: dict | None, *, now: dt.datetime) -> RunState   # pure
  def oom_error(memory_mb: int) -> str      # executor.py, if K-4 did not add it
  ```

- [ ] **Step 1: Failing tests — one per row**, evaluated in this order (the first matching row wins). "Pod" is the Job's newest pod; "container" its `run` container status; `detail` is truncated to `DETAIL_MAX_CHARS`.

  | # | The API says | `RunState` | Test |
  |---|---|---|---|
  | 1 | `GET` Job → 404 | `lost`, detail `"the run's Job no longer exists"` | `test_a_deleted_job_is_lost` |
  | 2 | Job `Complete=True` | `exited`, `ExitStatus(0)` | `test_a_complete_job_exited_zero` |
  | 3 | `Failed=True`; pod condition `DisruptionTarget=True`, or pod `status.reason == "Evicted"` | `lost`, detail `f"{DISRUPTION_EXIT} ({reason}): {message}"` | `test_a_disrupted_pod_is_lost_with_its_reason` |
  | 4 | `Failed=True`; pod `status.reason == "DeadlineExceeded"`, or the Job condition's reason is `DeadlineExceeded` | `exited`, `ExitStatus(<terminated exitCode> or 137, timed_out=True)` | `test_a_pod_past_its_deadline_is_timed_out_with_its_exit_code`, `test_a_job_past_its_deadline_is_timed_out` |
  | 5 | `Failed=True`; container `terminated.reason == "OOMKilled"` | `exited`, `ExitStatus(exitCode or 137, error=oom_error(<memory_mb from the container's limits>))` | `test_an_oom_kill_names_the_memory_limit` |
  | 6 | `Failed=True`; container `terminated` | `exited`, `ExitStatus(exitCode, error=None if reason in ("Error", "Completed") else f"{reason}: {message}")` | `test_a_failed_job_exits_with_the_containers_code` |
  | 7 | `Failed=True`; no pod or no terminated state | `exited`, `ExitStatus(-1, error=f"the run's Job failed ({reason}): {message}")` | `test_a_failed_job_without_a_pod_has_no_exit_code` |
  | 8 | `metadata.deletionTimestamp` set (no terminal condition) | `lost`, detail `"the run's Job is being deleted outside the platform"` | `test_a_job_being_deleted_is_lost` |
  | 9 | `spec.suspend`; no Workload (or the Workload API answers 404 — Kueue absent) | `pending_capacity`, detail `None` | `test_a_just_created_job_is_pending`, `test_a_missing_kueue_api_reads_as_no_workload` |
  | 10 | `spec.suspend`; Workload `spec.active == false` | `exited`, `ExitStatus(-1, error=f"Kueue deactivated the run: {message}")` (K5-R8) | `test_a_deactivated_workload_spends_an_attempt` |
  | 11 | `spec.suspend`; Workload `Evicted=True` (any reason) and not `QuotaReserved=True` | `pending_capacity`, detail `f"preempted: {message}"` when the reason is `Preempted`, else `f"evicted ({reason}): {message}"` (K5-R6) | `test_a_preempted_job_is_pending_capacity_not_lost` |
  | 12 | `spec.suspend`; `QuotaReserved=False` | `pending_capacity`, detail = its `message` | `test_quota_pending_carries_kueues_message` |
  | 13 | `spec.suspend`; `QuotaReserved=True` | `starting`, detail `None` (Kueue is unsuspending it) | `test_an_admitted_job_not_yet_unsuspended_is_starting` |
  | 14 | running Job, no pod | `starting`, detail `None` | `test_an_unsuspended_job_without_a_pod_is_starting` |
  | 15 | pod `PodScheduled=False`, reason `Unschedulable` | `pending_capacity`, detail `f"waiting for a node: {message}"` (K5-R9) | `test_an_unschedulable_pod_is_waiting_for_capacity` |
  | 16 | container `waiting.reason` in `IMMEDIATE_START_FAILURES` | `exited`, `ExitStatus(-1, error=f"the run could not start ({reason}): {message}")` | `test_an_invalid_image_fails_at_once` |
  | 17 | `waiting.reason` in `GRACED_START_FAILURES` | `now − (status.startTime or creationTimestamp) < 300 s` ⇒ `starting`, detail `f"{reason}: {message}"`; otherwise `exited` as row 16 (K5-R7) | `test_a_pull_failure_is_starting_within_the_grace`, `test_a_pull_failure_past_the_grace_exits` |
  | 18 | Job `status.ready >= 1`, or container `state.running` | `running`, detail `None` | `test_a_ready_pod_is_running` |
  | 19 | anything else (`ContainerCreating`, `PodInitializing`, a pod that finished before the Job controller noticed) | `starting`, detail = the waiting reason or `None` | `test_a_creating_container_is_starting` |

  Plus: `test_status_raises_when_the_api_is_down` (500 on the Job `GET`, on the pod list, and on the Workload list each ⇒ `ExecutorUnavailable`, never `lost`); `test_details_are_truncated` (a 2000-char Kueue message ⇒ 500 chars); `test_status_reads_only_what_it_needs` (a `Complete` Job ⇒ exactly one call; a suspended Job ⇒ Job `GET` + Workload list, no pod list; a running Job ⇒ Job `GET` + pod list, no Workload list — except rows 3–7, which need the pod).

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_kubernetes_executor_status.py -q`.

- [ ] **Step 3: Implement** `status` as: `GET` the Job (404 ⇒ row 1); if it is `Complete`, stop there; if it is `Failed`, or neither suspended nor being deleted, list its pods (newest); if it is suspended, list Workloads with `labelSelector=kueue.x-k8s.io/job-uid=<uid>` (newest; `KubeNotFound` ⇒ `None`); then `_job_state(job, workload, pod, now=self._clock())`, a pure function implementing the table top to bottom so the table's tests can also call it directly with hand-built dicts. `memory_mb` for row 5 is parsed from the container's `resources.limits.memory` (`"<n>Mi"`). Module docstring gains the table in prose: Kueue owns `pending_capacity`, the kubelet owns `starting`/`running`, the Job controller owns `exited`, and only an affirmed absence or a disruption is `lost`.

- [ ] **Step 4 (only if K-4 lacks it — Ask 3):** add `oom_error(memory_mb) -> str` to `executor.py` returning exactly K-4's Docker OOM wording; point `DockerExecutor.status` at it; K-4's `test_status_names_an_oom_kill` keeps passing unchanged.

- [ ] **Step 5 (only if K-4 lacks it — Ask 1):** in `reconcile.py` row 5, `error = state.detail or <K-4's fixed text>`; add `test_a_lost_run_records_the_backends_reason` to `test_process_reconcile.py` (a `MemoryExecutor` state `RunState("lost", detail="the run's pod was disrupted (Evicted): …")` ⇒ the requeued row's `error` is that detail).

- [ ] **Step 6: Gates and commit.**
  ```bash
  git commit -m "K-5: KubernetesExecutor.status — Kueue quota and unschedulable pods are pending_capacity, preemption stays submitted, disruption is lost, deadline/OOM/pull failures exit with reasons"
  ```

### Task 7: `watch()` — Job and Workload watches as K-4 wake-ups

**Files:**
- Modify: `services/pipeline/src/pipeline/process/kubernetes_executor.py` (`watch`, pure `_job_event`, `_workload_event`)
- Test: `services/pipeline/tests/test_kubernetes_executor_watch.py` (new)

**Interfaces:**
- Consumes: Task 3's `KubeApi.watch(..., idle_timeout=)`, `KubeGone`; K-4's `RunEvent`, `RESYNC`, `run_watcher` (C3, C8).
- Produces:
  ```python
  WATCH_TIMEOUT_SECONDS = 300
  WATCH_IDLE_SECONDS = 360
  async def watch(self) -> AsyncIterator[RunEvent]          # supports_watch is True
  def _job_event(obj: object) -> RunEvent | None           # label run-id + run-kind; handle = metadata.name
  def _workload_event(obj: object) -> RunEvent | None      # owner Job's name → parse_job_name
  ```
  The events carry identity only, never state (K-4 R1): the Reconciler re-reads `status()`.

- [ ] **Step 1: Failing tests.**
  - `test_watch_yields_resync_first_then_job_events` — consume `watch()` in a task; `[RESYNC]` first; then `submit` a run and `set_job_status(name, ready=1)` ⇒ `RunEvent(RUN, handle_id=name, kind="process")` for the ADDED and the MODIFIED.
  - `test_workload_events_map_to_their_job` — `set_workload(name, conditions=[QuotaReserved False Pending "…"])` ⇒ `RunEvent(RUN, name, "process")`.
  - `test_scan_job_events_carry_their_kind` ⇒ `kind="image_scan"` (K-4's `on_event` drops them).
  - `test_the_first_watch_starts_at_the_lists_resource_version` — the first `?watch=1` call's `resourceVersion` equals the preceding `limit=1` LIST's, and it sends `allowWatchBookmarks=true`, `timeoutSeconds=300`, and the run-id label selector (Jobs only).
  - `test_a_normal_close_resumes_from_the_last_bookmark` — `fake.bookmark()` then `fake.close_watches()`: no second RESYNC is yielded, and the next watch call's `resourceVersion` is the bookmark's.
  - `test_a_410_ends_the_stream_so_the_watcher_resyncs` — `fake.compact()` + `close_watches()` ⇒ the resumed watch gets `ERROR 410` and `watch()` raises `KubeGone`.
  - `test_silence_past_the_idle_timeout_raises` — monkeypatch `WATCH_IDLE_SECONDS` to 0.2 ⇒ `ExecutorUnavailable`.
  - `test_a_missing_kueue_api_watches_jobs_only` — the Workload LIST answers 404 ⇒ RESYNC and Job events still flow; no Workload watch is opened.
  - `test_with_run_watcher_every_connect_resyncs` — K-4's `run_watcher(executor.watch, on_event, reconnect_delay=0, max_connects=2)` across one forced 410 ⇒ `on_event` saw `RESYNC`, …, `RESYNC`, … in that order.
  - `test_strangers_and_garbage_are_ignored` — `_job_event` of a Job without `stac-higher.run-id`, a non-dict, a `Status` object ⇒ `None`; `_workload_event` without a Job owner or with an owner name `parse_job_name` rejects ⇒ `None`.
  - `test_supports_watch` — `KubernetesExecutor.supports_watch is True`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_kubernetes_executor_watch.py -q`.

- [ ] **Step 3: Implement.** On each call: in a thread, `GET` Jobs with the run-id selector and `limit=1` (its `metadata.resourceVersion`), and the same for Workloads (no selector; a `KubeNotFound` ⇒ no Workload watch); `yield RESYNC`. Then one pump task per watch, each looping `async for ev in api.watch(path, query={…, "resourceVersion": rv}, idle_timeout=WATCH_IDLE_SECONDS)`: record `ev["object"]["metadata"]["resourceVersion"]` as `rv`; skip `BOOKMARK`; map ADDED/MODIFIED/DELETED through `_job_event`/`_workload_event` onto an `asyncio.Queue`; when the server ends the stream normally, loop and resume from `rv`. The generator yields from the queue and, as soon as either pump task finishes with an exception (`KubeGone`, `ExecutorUnavailable`), cancels the other and re-raises it — so K-4's `run_watcher` logs, waits, reconnects and RESYNCs (K5-R21). `finally:` cancels both pumps. No thread holds a stream; no pod watch.

- [ ] **Step 4: Gates and commit.**
  ```bash
  git commit -m "K-5: KubernetesExecutor.watch — Job + Workload watches from a listed resourceVersion, bookmarks, resume on a normal close, 410/idle/transport errors end the stream for a RESYNC"
  ```

### Task 8: `build_executor` everywhere; capacity gate; reaper ceiling; `/health` executor key

**Files:**
- Create: `services/pipeline/src/pipeline/process/factory.py`
- Modify: `services/pipeline/src/pipeline/jobs/process.py` (`_execute_claimed`, `process_reap`, and K-4's `build_reconciler` default, `build_run_watcher`, `build_cancel_listener` — every `DockerExecutor(` goes)
- Modify: `services/pipeline/src/pipeline/jobs/image_scans.py` (`executor = build_executor(settings)`)
- Modify: `services/pipeline/src/pipeline/process/runner.py` (K-3's capacity block runs only when `executor.enforces_local_capacity`)
- Modify: `services/pipeline/src/pipeline/process/reaper.py` (`reap_max_age_seconds`)
- Modify: `services/pipeline/src/pipeline/health.py` (`executor_status` parameter, `executor` key)
- Modify: `services/pipeline/src/pipeline/main.py` (pass an `ExecutorProbe` to `create_health_app`)
- Test: `services/pipeline/tests/test_executor_factory.py` (new), `tests/test_process_capacity.py`, `tests/test_process_reaper.py`, `tests/test_health.py`, `tests/test_main_jobs.py` (append each)

**Interfaces:**
- Consumes: Task 1's settings; Task 4's `enforces_local_capacity`, `probe()`; Task 5's `KubernetesExecutor.from_settings`.
- Produces:
  ```python
  # process/factory.py
  def build_executor(settings: Settings) -> Executor            # DockerExecutor | KubernetesExecutor; no I/O
  EXECUTOR_PROBE_TTL_SECONDS = 30
  class ExecutorProbe:
      def __init__(self, settings: Settings, *, ttl_seconds: float = EXECUTOR_PROBE_TTL_SECONDS,
                   timeout_seconds: float = 5.0, clock: Callable[[], float] = time.monotonic,
                   build: Callable[[Settings], Executor] = build_executor) -> None
      async def __call__(self) -> dict[str, Any]   # {"name": str, "reachable": bool, "error": str | None}

  # process/reaper.py
  def reap_max_age_seconds(profiles: HardwareProfileSet | None) -> int

  # health.py
  def create_health_app(queue, heartbeat_state=STATE, *, image_policy_file=None,
                        executor_status: Callable[[], Awaitable[dict[str, Any]]] | None = None) -> FastAPI
  ```

- [ ] **Step 1: Failing tests.**
  - `test_executor_factory.py`:
    - `test_docker_is_the_default` — `build_executor(Settings())` is a `DockerExecutor` on `settings.docker_host`.
    - `test_kubernetes_is_selected_by_setting` — `PROCESS_EXECUTOR=kubernetes` with `PROCESS_K8S_API_URL=http://127.0.0.1:1`, a token file ⇒ a `KubernetesExecutor` with the settings' namespace, queue, service account and mirrors; building it makes no network call (the port is closed and nothing raises).
    - `test_kubernetes_outside_a_cluster_is_unavailable` — no API URL ⇒ `build_executor` raises `ExecutorUnavailable` naming `PROCESS_K8S_API_URL` (the tick that called it fails; startup does not — K5-R18).
    - `test_no_inline_executor_constructions` — no `*.py` under `src/pipeline/` other than `process/factory.py` and `process/docker_executor.py` contains `DockerExecutor(` or `KubernetesExecutor(`.
    - `test_the_probe_reports_reachable_and_caches` — a stub executor whose `probe` counts calls: two calls inside the TTL ⇒ one probe; `{"name": "kubernetes", "reachable": True, "error": None}`; after the TTL ⇒ probed again.
    - `test_the_probe_reports_an_outage_without_raising` — `probe` raises `ExecutorUnavailable("…")`, `build` raises, and `probe` sleeps past `timeout_seconds`: each ⇒ `reachable: False` with the error text (≤ 300 chars).
  - `test_process_capacity.py`: `test_a_backend_without_local_capacity_skips_the_gate` — a full `cpu-large` profile (K-3's existing setup) with a `MemoryExecutor` whose class attribute is overridden to `enforces_local_capacity = False` (a subclass in the test) ⇒ the run is submitted, not released.
  - `test_process_reaper.py`: `test_the_reap_ceiling_counts_the_longest_queue_wait` — profiles with waits 900 and 7200 ⇒ `MAX_TIMEOUT_SECONDS + 600 + 7200 + 3600`; `None` ⇒ `DEFAULT_REAP_MAX_AGE_SECONDS`; and the existing "ceiling sits above the max timeout" test still holds.
  - `test_health.py`: `test_health_reports_the_executor` — `create_health_app(queue, executor_status=stub)` ⇒ body `executor == stub()`'s dict, status 200 even when `reachable` is false; without `executor_status` the key is `None`.
  - `test_main_jobs.py`: `test_health_gets_an_executor_probe` — the seam K-4's `test_run_passes_the_watcher_and_the_cancel_listener` uses (monkeypatched `build_queue` with a queue whose `setup` succeeds, `run_until_first_exit` capturing and closing its coroutines), plus a monkeypatched `create_health_app` capturing its kwargs ⇒ `executor_status` is an `ExecutorProbe`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_executor_factory.py tests/test_process_capacity.py tests/test_process_reaper.py tests/test_health.py tests/test_main_jobs.py -q`.

- [ ] **Step 3: Implement.**
  - `build_executor`: `settings.process_executor == "kubernetes"` ⇒ `KubernetesExecutor.from_settings(settings)`, else `DockerExecutor(docker_host=settings.docker_host)`. Import `KubernetesExecutor` inside the branch so a Docker deployment never imports it.
  - `ExecutorProbe.__call__`: within the TTL return the cached dict; otherwise `executor = self._build(settings)` (an exception ⇒ unreachable), then `await asyncio.wait_for(asyncio.to_thread(executor.probe), self._timeout)`; `name` is `settings.process_executor` (so a build failure still names the backend).
  - Replace every inline construction: `_execute_claimed`, `process_reap` (which also passes `max_age_seconds=reap_max_age_seconds(profiles)` — load the profile set with `load_hardware_profiles()`, `None` on `HardwareProfileError`, logged at WARNING once per tick), K-4's `build_reconciler(…, executor=None)` default, `build_run_watcher`, `build_cancel_listener`, and `image_scans.py`. Each call site keeps its own try/except shape: an `ExecutorUnavailable` from `build_executor` in a tick is logged like any other executor outage (K-4's existing handling around those builders); `build_run_watcher` with an unbuildable executor returns K-4's wait-forever coroutine after a WARNING (the tick is the backstop).
  - `runner.py`: wrap K-3's capacity block in `if executor.enforces_local_capacity:`; comment names K5-R19.
  - `health.py`: `"executor": await executor_status() if executor_status else None`; module docstring notes it is informational like `image_policy`.
  - `main.py`: `create_health_app(queue, executor_status=ExecutorProbe(settings))`.

- [ ] **Step 4: Gates and commit.** All of pytest (the factory moved every call site); ruff.
  ```bash
  git commit -m "K-5: build_executor(settings) replaces every inline DockerExecutor; local capacity only on Docker; reaper ceiling counts the queue wait; /health executor key"
  ```

### Task 9: Least privilege, proven both ways; the hand-off note for R-8 and K-6

Every executor test since Task 5 ran against a fake that refuses anything the Role does not grant — that is sufficiency. This task proves necessity (every grant is used) and pins the manifests' identities to the settings.

**Files:**
- Create: `services/pipeline/tests/test_kubernetes_rbac.py`
- Create: `infra/kubernetes/README.md`

**Interfaces:**
- Consumes: Task 2's manifests and `FakeKube.used`; Tasks 5–7's executor.

- [ ] **Step 1: Failing tests** (`test_kubernetes_rbac.py`):
  - `test_one_lifecycle_uses_every_grant_and_nothing_else` — against one `FakeKube`, drive the executor through: `probe`; `submit` (with a registry credential, so both Secrets); `status` while suspended (Workload list) and while running (pod list); one event from `watch()` (both watches open); `cancel` of a running pod (patch); `logs`; `reap`; `list_launched`; a second run cancelled while suspended (Job delete). Then `fake.used` equals the set of `(group, resource, verb)` triples the Role grants — exactly.
  - `test_the_role_is_namespaced_and_has_no_wildcards` — `kind: Role` (never `ClusterRole`), `metadata.namespace == DEFAULT_PROCESS_K8S_NAMESPACE`, no `*` in any `apiGroups`/`resources`/`verbs`, and no `get`/`list`/`watch` on `secrets`.
  - `test_the_manifests_agree_with_the_settings_and_the_chart` — the Namespace name and the run ServiceAccount name equal Task 1's defaults; the Namespace enforces Pod Security `restricted`; the ServiceAccount has `automountServiceAccountToken: false` and no `eks.amazonaws.com/role-arn` annotation; the RoleBinding's subject is ServiceAccount `identity.serviceAccounts.pipeline` in `identity.namespace` from `infra/helm/stac-higher/values.yaml`.
  - `test_a_built_pod_satisfies_pod_security_restricted` — Task 4's pod spec checked against the `restricted` profile's rules in code (non-root, `RuntimeDefault` seccomp, `drop: [ALL]`, no privilege escalation, no host namespaces, ports or paths, volumes of type `emptyDir` only) so a manifest change that the namespace would reject fails here, not on a cluster.

- [ ] **Step 2: Run to verify they fail, then pass** once the lifecycle test is written against the existing executor (no production code is expected to change; if a grant is unused, remove it from the Role — or the code path that should use it is missing).

- [ ] **Step 3: `infra/kubernetes/README.md`** (short): what each file is; `kubectl apply -f infra/kubernetes/` creates the runs namespace, its PSA label, the token-less run ServiceAccount and the pipeline's Role/RoleBinding — nothing else (no NetworkPolicy, LocalQueue or Kueue objects: R-8 #144 / K-6 #14); the RoleBinding's subject is the chart's default pipeline ServiceAccount, edit it for another namespace; **R-8 renders the same four objects from chart templates and must keep a drift test against these files**; K-6 applies them to kind and mints the test token with `kubectl create token stac-higher-pipeline -n stac-higher`; the pipeline's settings that must agree (`PROCESS_K8S_NAMESPACE`, `PROCESS_K8S_RUN_SERVICE_ACCOUNT`); and the rule that the run ServiceAccount never gets a Pod Identity association or IRSA annotation.

- [ ] **Step 4: Gates and commit.**
  ```bash
  git commit -m "K-5: least privilege proven — one lifecycle exercises every Role grant and nothing else; manifests pinned to the settings, the chart's identities and Pod Security restricted"
  ```

### Task 10: The chart and the app learn the backend

**Files:**
- Create: `app/src/lib/processes/executor.ts`
- Modify: `app/src/pages/api/processes/hardware-profiles.ts` (drop `EXECUTOR_BACKEND`; call `getExecutorBackend()` per request)
- Modify: `infra/helm/stac-higher/templates/config-env.yaml` (`$pipe` gains two keys)
- Modify: `infra/helm/stac-higher/values.yaml` (comments on `processRuns.namespace` and `registry.mirrorPrefixes` name the variables they now feed)
- Modify: `infra/helm/stac-higher/README.md` (the values table rows for those two keys; a "Process runs" note: the pipeline runs the Kubernetes executor, R-8's objects are a prerequisite, drain runs before switching `PROCESS_EXECUTOR`)
- Test: `app/src/__tests__/processes-executor.test.ts` (new), `app/src/__tests__/contract-fixtures.test.ts` (append), `app/src/__tests__/api-processes.test.ts` (the hardware-profiles route block — append), `app/src/__tests__/helm/config.test.ts` (append)

**Interfaces:**
- Consumes: Task 1's fixture.
- Produces:
  ```ts
  // app/src/lib/processes/executor.ts
  export const PROCESS_EXECUTOR_BACKENDS = ["docker", "kubernetes"] as const;
  export type ExecutorBackend = (typeof PROCESS_EXECUTOR_BACKENDS)[number];
  export const DEFAULT_EXECUTOR_BACKEND: ExecutorBackend = "docker";
  export function getExecutorBackend(env?: Record<string, string | undefined>): ExecutorBackend; // throws on an unknown value
  ```
  Chart: `PROCESS_K8S_NAMESPACE` = `processRuns.namespace`; `PROCESS_K8S_REGISTRY_MIRRORS` = compact JSON of `registry.mirrorPrefixes` with `ghcr.io` replaced by `global.imageRegistry` when that is set (the `stac-higher.mirrorRef` rule), omitted when the result is empty.

- [ ] **Step 1: Failing tests.**
  - `processes-executor.test.ts` (the `network.ts` test shape): unset ⇒ `"docker"`; `" Kubernetes "` ⇒ `"kubernetes"`; `"ecs"` throws naming `PROCESS_EXECUTOR` and both values.
  - `contract-fixtures.test.ts`, `describe("process executor contract")`: `PROCESS_EXECUTOR_BACKENDS` equals `fixture.backends`; `DEFAULT_EXECUTOR_BACKEND` equals `fixture.default`.
  - `api-processes.test.ts` (where the hardware-profiles route is tested): `backend` is `"kubernetes"` under `vi.stubEnv("PROCESS_EXECUTOR", "kubernetes")`, `"docker"` by default; an unknown value is a 500 whose body names the variable (the route's existing catch).
  - `helm/config.test.ts`: `it("the pipeline gets the runs namespace and the registry mirror map")` — base render ⇒ `PROCESS_K8S_NAMESPACE === "stac-higher-runs"` and no `PROCESS_K8S_REGISTRY_MIRRORS`; `registry.mirrorPrefixes {ghcr.io: A, docker.io: B}` ⇒ `JSON.parse(...)` equals both; plus `global.imageRegistry: C` ⇒ `ghcr.io` is `C`; `processRuns.namespace: other` ⇒ `"other"`; and the `digests-mirror` scenario's `PROCESS_RUNTIME_IMAGE` stays the upstream reference (the executor mirrors it at Job creation — deployment spec §5).

- [ ] **Step 2: Run to verify they fail** — `npx vitest run src/__tests__/processes-executor.test.ts src/__tests__/contract-fixtures.test.ts src/__tests__/api-processes.test.ts src/__tests__/helm/config.test.ts` (from `app/`; the helm file skips without `helm` — say so).

- [ ] **Step 3: Implement** the module (mirroring `network.ts`: trim, lower-case, validate against the tuple, throw with both values), the route change, and the two template keys (`toJson` of a `deepCopy`'d dict; `$_ := set` when `global.imageRegistry` is non-empty, the same condition `stac-higher.mirrorRef` uses).

- [ ] **Step 4: Gates and commit.** `npm run verify`.
  ```bash
  git commit -m "K-5: the hardware-profiles route reports PROCESS_EXECUTOR; the chart passes the runs namespace and the registry mirror map to the pipeline"
  ```

### Task 11: Docs

**Files:** `docs/FEATURES.md`, `docs/ISSUES.md`, `docs/processes.md`, `docs/backend.md`, `services/pipeline/README.md`, `tests/contract-fixtures/README.md` (Task 1 did).

- [ ] **FEATURES.md:** a `K-5 · Kubernetes executor + build_executor` row after K-4: Job + owned Secret per submission (K5-R2/R3), Kueue labels, pod lockdown and pod-level deadline (K5-R4), `status()`'s mapping (Kueue quota / unschedulable ⇒ waiting for capacity; preemption stays submitted; disruption ⇒ lost; deadline/OOM/pull failures exit), Job + Workload watches, cancel by pod deadline (K5-R5), the registry mirror map, `infra/kubernetes/` RBAC proven by the fake, `build_executor`, `/health` `executor`, the `process-executor.json` fixture; "unit-tested against a fake API server only — the first real cluster is K-6's kind leg; EKS is R-11".
- [ ] **ISSUES.md** (on `main`'s numbering — take the next free I-numbers):
  - **I-160** → the pipeline half resolved (K-5); what remains before a cluster can run a process: R-8's namespace objects, NetworkPolicies and Kueue objects (#144), K-6's Kueue manifests (#14). Keep `Tracked in: GitHub #144`.
  - **I-169**: "Before K-5" → "before R-8 and K-6 are deployed (R-11)".
  - **I-94**: add the kubelet log-rotation consequence (K5-R26) and that a preempted execution's log is lost (K5-R6).
  - **I-124** (as K-4 left it): admission scans on Kubernetes also wait in Kueue and their timeout counts that wait (K5-R11).
  - **New entries:** (a) run pods see `KUBERNETES_SERVICE_HOST/PORT` (no token; K5-R14); (b) a user image that writes into its own `WORKDIR` fails on Kubernetes — read-only root (K5-R13); (c) on Kubernetes a run's duration and `pipeline_process_run_seconds` include its queue wait — no `running_at` (K5-R16, K-4 R5); (d) switching `PROCESS_EXECUTOR` strands in-flight runs — drain first (K5-R25); (e) an unpullable image costs ~6 minutes and an attempt per try (K5-R7); (f) a run pod waiting for its image after scheduling spends the credentials' grace, which K-4's requeue rule does not see (only `pending_capacity` is checked); (g) the scanner's Grype DB update cannot write on Kubernetes and falls back to the baked DB (K5-R13, #68); (h) queue position (`positionInLocalQueue`) is not read — the visibility API is deferred (K5-R10); (i) a Kueue `Inadmissible` workload (e.g. a profile naming a LocalQueue that does not exist) pends until K-4's credential requeue, then repeats — visible only as the row's detail.
- [ ] **processes.md:** a "Running on Kubernetes" subsection under "Where your code runs": *Waiting for capacity* means the deployment's quota is full **or** a node is being started (minutes for a GPU); a higher-priority test run may preempt a triggered run, which then waits again without spending an attempt and restarts from the beginning; the root filesystem is read-only — write to `/tmp` (and, on the platform image, the working directory), sized by `memory_mb` and counted against it; `HOME` is `/tmp` on your own image; the timeout and Cancel behave as on Docker (10 s to exit); a private image is pulled from its registry directly. In "When does a run start?": the extra wait states.
- [ ] **backend.md:** the services table or deployment section: on a cluster there is no socket proxy; the pipeline's ServiceAccount drives Jobs in `stac-higher-runs` through `infra/kubernetes/`'s Role (`PROCESS_EXECUTOR=kubernetes`).
- [ ] **pipeline README:** environment-contract rows for `PROCESS_EXECUTOR` and the seven `PROCESS_K8S_*` settings (defaults from Task 1); the "Health endpoint" section gains `executor` (`{name, reachable, error}`, informational, cached 30 s); a short "Kubernetes executor (K-5)" section: one run = Job + Secret, the status table in five lines, the watch, RBAC in `infra/kubernetes/`, the fake API server as the test bed.
- [ ] Gates (`npm run verify`) and commit: `git commit -m "K-5: docs — FEATURES row, I-160/I-169/I-94/I-124 updated, Kubernetes residuals, running on Kubernetes, executor settings"`.

### Task 12: Verify, PR, hand-offs, live checks (lead only)

- [ ] Worktree: `npm run verify`; `uv run pytest -q`; `uv run ruff check .`. Rebase onto `main`; gates again. Push; `gh pr create --base main` titled `K-5: Kubernetes executor (Job + Secret per run, Kueue-aware status and watch), build_executor, /health executor key`, body starting `Closes #13`, listing the gates, "no migration", the deviations from spec/issue text (K5-R2 Job naming, K5-R4 deadline on the pod, K5-R5 cancel mechanism, K5-R10 RBAC verbs, K5-R11 scans on Jobs, K5-R12 pull Secret), any K-4 contract differences found in Task 0, and the lead-only steps below. Squash-merge when CI is green.
- [ ] **Compose regression (the factory is on every path):** `docker compose up -d --build pipeline`; `/health` shows `executor: {name: "docker", reachable: true, error: null}`; a `standard` run and an image scan still succeed and reap; the GOES demo loop stays healthy (`uv run python -m pipeline.demo status` from `services/pipeline`).
- [ ] **Hand-off to K-6 (#14)** — comment on the issue with the first things the kind leg must prove, in order, because no real API server has seen this code: (1) TLS to the API server with the mounted CA and a `kubectl create token` bearer; (2) the Role is sufficient under the real authorizer (run the lifecycle against kind); (3) Kueue's webhook suspends our Jobs, creates a Workload labelled `kueue.x-k8s.io/job-uid`, and reports `QuotaReserved=False` with a message under a 1-CPU quota; the Workload API version (`KUEUE_API_VERSION`); (4) **K5-R5:** after the cancel patch the pod ends `Failed`/`DeadlineExceeded` within ~10 s and `pods/log` still serves its output; (5) **K5-R4:** a run past its timeout keeps its log; (6) a watch survives `timeoutSeconds`, receives bookmarks, and a 410 after compaction triggers a RESYNC; (7) Pod Security `restricted` and K-6's ValidatingAdmissionPolicy admit our Jobs; (8) `interactive` preempting `triggered` reads as `pending_capacity` → readmitted, no attempt spent. Any failure of (4) or (5) means falling back to K-4's row-1 reorder (logs before cancel) — raise it on the epic.
- [ ] **Hand-off to R-8 (#144)** — comment: render `infra/kubernetes/`'s four objects from chart templates with a drift test against the files; the run ServiceAccount must never get a Pod Identity association; NetworkPolicies select `stac-higher.run-kind` (`process` ⇒ DNS + `aws.s3PrefixListCidrs`; `image_scan` ⇒ + 443); `PROCESS_K8S_REGISTRY_MIRRORS` is already wired (Task 10) — R-8 keeps the uncached-by-digest ECR test; nothing else in the executor is R-8's.
- [ ] **Hand-off to #68** — comment: scan pods are built by `kubernetes_manifests.build_job` (read-only root, `/tmp` and `/home/scanner` on memory, uid 10001, no token); the Grype DB update falls back to the baked DB; the egress proxy's `HTTPS_PROXY` must reach the scanner through the env Secret (a `settings`-driven addition to `scan_env`, not the Job spec).
- [ ] **EKS (R-11, #147), when it runs:** a `standard` run end to end on Auto Mode (*Waiting for capacity* while a node starts, then `succeeded`); a pull through the ECR mirror by digest; `karpenter.sh/do-not-disrupt` honoured during a consolidation; a run pod cannot reach the instance metadata endpoint (with #68's policy); credential lifetime against the one-hour role-chaining cap (I-93).
- [ ] `git worktree remove .claude/worktrees/k5-kubernetes-executor`.

## Self-review

- **Spec coverage.** §6 factory + `/health` key → T8 (+ T1 settings, T10 app/chart). §6.1 Secret → T4/T5 (K5-R3 ordering, ownerReference, never logged); Job fields and labels → T4 (Global Constraints; deadline per K5-R4); pod template (envFrom, requests = limits, selector/tolerations, no token, securityContext, read-only root + emptyDirs) → T4; handle = Job name → T5 (K5-R2); `status()` Job + Workload (`QuotaReserved` → `pending_capacity` detail) → T6; `watch()` Job + Workload with resourceVersion bookkeeping → T7; `logs()` via `pods/log` capped → T5 (K5-R26); `cancel()` → T5 (K5-R5); `reap()`, `list_launched()` raising → T5. §6.2 network → R-8 (K5-R15). §6.3 RBAC → T2/T9 (K5-R10). §3.4 backend block → T4 (GPU mapping only, K5-R24). §7.2 Evicted → T6 (K5-R6). §8 kind leg → K-6 hand-off (T12). Issue extras: the two (now five, with K-4's builders and the scan drain) inline constructions → T8; fake API server → T2; image scans sharing the executor → K5-R11/T4; registry mirror map → T1/T4/T10 (K5-R12); credential-expiry requeue live on this backend → C13 + K5-R9; reaper ceiling → T8 (K5-R22).
- **Type consistency.** `JobContext` (T4) built by `KubernetesExecutor` from its fields (T5); `job_name`/`parse_job_name` (T4) used by submit (T5) and `_workload_event` (T7); `KubeApi.watch(..., idle_timeout=)` (T3) used by T7; `RunRejected` (T4) raised in T5 and mapped by K-4's existing `HardwareUnavailable` branch (C10); `enforces_local_capacity`/`probe()` (T4) used by T8; `PROCESS_EXECUTORS` (T1) = `PROCESS_EXECUTOR_BACKENDS` (T10) = the fixture; `FakeKube.used` (T2) read by T9; `oom_error` (K-4 or T6) used by T6 row 5.
- **Proportion.** Code appears only for the Role's rules (the security boundary, exact by design) and interface signatures; the status mapping is a table, not a function body.

## Addendum — Task 0 reconciliation against merged K-3/K-4 (2026-11-26)

Walked C1–C17 against `main` at `6ac3a03` (K-4, #204) and `f82ad9e` (K-3, #201). Each line: what the merged code does, the ruling, what it costs if wrong. Where a ruling changes a task, the task is read with it.

**Kueue.** v0.19.2's Workload CRD serves `v1beta1` (deprecated, `storage: false`) and `v1beta2` (`storage: true`). `v1beta2` keeps `spec.active`, the condition types `QuotaReserved` / `Admitted` / `Evicted`, the eviction reason `Preempted`, and the labels `kueue.x-k8s.io/job-uid`, `kueue.x-k8s.io/queue-name`, `kueue.x-k8s.io/priority-class` (`pkg/controller/constants`). `KUEUE_API_VERSION = "v1beta2"` stands; Task 6's table is unchanged.

**Contract points that hold as planned:** C1 (abstract set `submit, status, cancel, logs, reap, list_launched`; `watch` optional; `supports_watch`; default `submitted_phase = "pending_capacity"`), C3, C4, C7, C9 (all three builders, through one helper), C10, C12, C13 (`max(900, queue_wait + timeout + grace)`), C15 (plus `cancel_error`, `log_output`; Memory's `submitted_phase` is `"starting"`), C16.

**Differences and rulings:**

- **A1 · `RunState` gained `finished_at` (C2).** K-4 uses it to decide the "had already exited" suffix on a cancel (only an exit that predates the request). *Ruling:* `status()` fills it from the `run` container's `terminated.finishedAt`, else the Job's `Complete`/`Failed` condition `lastTransitionTime`, else `None`; a start failure (rows 16–17) and a Kueue deactivation (row 10) report `None`. *Cost if wrong:* a cancelled run that had already exited misses the suffix (cosmetic).
- **A2 · Ask 1, 2 and 3 are done (C6, C14).** Row 5 writes `state.detail or LOST_ERROR`; `oom_error(memory_mb: int | None)` is in `executor.py`; the stream reader is `process/http_stream.py` under the names `read_http_head`, `is_chunked`, `iter_body_lines`, `HttpStreamError` (not `read_response_head`). Task 3 Step 1 and Task 6 Steps 4–5 are no-ops; `kube_api.watch` uses those names.
- **A3 · `cancel()` returning does not mean stopped; the handle must be unique per submission (C5).** The ledger's compare-and-set fences key on the handle. K5-R2's per-submission suffix already makes the Job name unique; K5-R5's pod-deadline stop is asynchronous, which the contract now allows explicitly (the caller re-reads `status()`).
- **A4 · The claim stamps the backend, and the run path never passes it (C11, new).** `claim_due_runs` / `claim_run` take `backend=` (default `"docker"`) and stamp it as `executor_backend`; `record_submission` only matches a row whose `executor_backend` equals the submitting executor's name. `jobs/process.py` passes no `backend`, so every Kubernetes submission would fail its compare-and-set and be discarded. *Ruling:* Task 8 builds the executor before the claim and passes `backend=executor.name`; a test pins it. *Cost if wrong:* nothing on Kubernetes ever runs (loud, caught by the test).
- **A5 · Capacity moved from `run_one` into the claim (C17).** K-3/K-4 admit inside the claim (`_admit_and_claim`, per-profile advisory lock, `capacities=docker_capacities(profiles)`) and `run_one` no longer counts. *Ruling (K5-R19 re-targeted):* `Executor.enforces_local_capacity` (False; Docker and Memory True) decides whether `jobs/process.py` passes `capacities=docker_capacities(profiles)` or `None`. On Kubernetes, Kueue's ClusterQueue quota is the per-profile bound and the claim admits every profile run at once (it is `running` with phase `pending_capacity` until Kueue admits it). *Cost if wrong:* none known; a profile file carrying a `docker.capacity` on a cluster is ignored, as intended.
- **A6 · The exempt extractor pool stays on Kubernetes.** `PROCESS_EXTRACT_MAX_RUNNING` (chart `processRuns.extractMaxRunning`) bounds default-profile extractor runs deployment-wide in the claim. *Ruling:* keep it on every backend. Kueue does not tell an extractor Job from a process Job in the same LocalQueue, so without the pool an ingest burst could put an unbounded number of Jobs into the queue ahead of process runs. *Cost if wrong:* extractor throughput on a cluster is capped at the setting until an operator raises it.
- **A7 · `cpu_exempt` is a Docker-host concession.** K-3 launches default-profile extractor runs without `NanoCpus`. A Kubernetes pod with requests == limits (Guaranteed QoS, what Kueue counts) cannot be "uncapped". *Ruling:* on Kubernetes every run's CPU is `spec.cpu` (the profile default, 1.0 on `standard`); `cpu_exempt` is not read. *Cost if wrong:* a CPU-heavy hand-written extractor on `standard` runs slower on a cluster than on Docker.
- **A8 · `backend.docker` is parsed with the document (K-3 review).** A bad block makes the whole file unusable, which `run_one` treats as infrastructure (requeue, no attempt). *Ruling:* `backend.kubernetes` follows the same path: `HardwareProfile.kubernetes: KubernetesBackend` parsed in `_profile`, `kubernetes_backend(profile)` returns it; a malformed block raises `HardwareProfileError` naming the profile at load. Task 4's test drives `parse_hardware_profiles`.
- **A9 · The label constants still live in `docker_executor.py`.** Task 4 moves them to `executor.py` as planned; `docker_executor` re-exports them.
- **A10 · `run_watcher` has its own 11-minute idle timeout (C8).** It counts only yielded `RunEvent`s; a Kubernetes watch's bookmarks are not events. *Ruling:* no change. A quiet cluster reconnects the watcher (one RESYNC sweep) every 11 minutes, the same cadence at which Docker's socket proxy closes `/events`. The per-stream `WATCH_IDLE_SECONDS = 360` still catches a half-open connection sooner. *Cost if wrong:* one WARNING and one sweep per 11 quiet minutes.
- **A11 · Credential expiry requeue is live on Kubernetes (C13).** `credentials_expire_at` is on the row; row 3 fires only on `pending_capacity`, which K-5 reports for Kueue quota waits, preemption and unschedulable pods (K5-R9). Row 3's `cancel()` of a still-pending submission deletes the Job (K5-R5: no started pod), so the resubmission gets a new Job and a new handle.
- **A12 · The reaper's ceiling (I-176).** K-4 recorded it as K-5's; Task 8 implements K5-R22 and Task 11 resolves I-176's text. The ledger rule is unchanged: a Job whose row is `running` is kept, any other is reaped.
- **A13 · The DB pool rule is `DB_POOL_MAX >= WORKER_CONCURRENCY + 6`.** K-5 adds no DB use: the watch, the probe and the executor hold no connection.
- **A14 · No migration.** The 029 phase CHECK admits `pending_capacity`, `starting`, `running`; K-5 writes nothing else to the row (`exited`/`lost` are never stored). Nothing reserved on #13; none needed.
- **A15 · The ISSUES numbering.** K-4's residuals are I-173..I-177; Task 11's new entries start at I-178. I-176 is resolved in place (the ceiling lands here).
- **A16 · `/health`'s probe and the blocking executor.** `ExecutorProbe` runs `probe()` through `asyncio.to_thread` on the loop's default executor (sized to the job slots by `main.blocking_executor`); one probe per 30 s TTL is negligible against it.
- **A17 · The executor is built before the claim.** A Kubernetes executor that cannot be built (no API URL) raises `ExecutorUnavailable` from `build_executor`; the run tick and `process_run_now` then log a WARNING and claim nothing (a claim that cannot submit would only requeue). The reconcile sweep is skipped for the same reason. `build_run_watcher` falls back to K-4's idle coroutine; the reaper tick logs and returns.

**Lead-held:** assigning #13 and removing `blocked` are GitHub writes the implementer does not make.



