# K-6 · Kueue Manifests and a kind CI Leg — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub issue #14 (K-6) · epic #2 · **no migration** · blocked by #13 (K-5).

**Goal:** The Kueue objects every Kubernetes deployment of the platform needs live under `infra/kueue/` (pinned Kueue release, its controller configuration, priority classes, LocalQueue, the admission policy that refuses a Job without a queue, and the kind cluster's flavor and ClusterQueue); `infra/hardware-profiles/kind.json` describes a one-CPU deployment; and a CI job creates a kind cluster, installs Kueue, applies those objects and K-5's `infra/kubernetes/` RBAC, and runs the Kubernetes executor's integration tests **as the pipeline's ServiceAccount**, so K-5's code meets a real API server, a real kubelet and a real Kueue for the first time.

**Architecture:** Nothing in `services/pipeline/src/` changes unless the kind leg disproves a K-5 assumption (then a separate `fix/` PR, Ruling K6-R12). New files are manifests (`infra/kueue/`, `infra/kind/`), three shell scripts (`scripts/kind-up.sh`, `scripts/kind-diagnostics.sh`, `scripts/check-kueue.sh`), one workflow (`.github/workflows/kind.yml`), a static pytest module that runs in the ordinary pipeline job, and two integration modules plus a harness that run only when `KIND_INTEGRATION=1`. The tests run on the CI runner and reach kind's API server at `https://127.0.0.1:6443` with the cluster CA and a bound token for `stac-higher-pipeline` (K6-R3, K6-R4); a separate admin kubeconfig is used only by the harness, through `kubectl`, for fault injection and observation. The run image is the real `process-runtime` base image, built from its own context and `kind load`ed (K6-R5).

**Tech Stack:** Kueue **v0.20.8** (`kueue.x-k8s.io/v1beta2`, controller config `config.kueue.x-k8s.io/v1beta2`), kind **v0.33.0** with node image **kindest/node v1.34.11**, kubectl **v1.34.11**, `admissionregistration.k8s.io/v1` ValidatingAdmissionPolicy, kubeconform v0.8.0 with schemas generated from Kueue's own CRDs, pytest + pytest-asyncio (existing), PyYAML (tests only, already in the lock per K-5 Task 2), docker buildx bake (existing `services/process-runtime/docker-bake.hcl`). **No new Python or npm dependency.**

**Spec:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §2 (the CI leg), §3.4 (`backend.kubernetes`), §6.1–6.3, §7 (Kueue topology), §8 (local and CI parity), §15 (I-92); ADR 0019; ADR 0013 (isolation). Predecessor plan, **the contract**: `docs/superpowers/plans/2026-11-25-k5-kubernetes-executor.md` (on `main` once K-5 merges; until then `git show feat/k5-kubernetes-executor:docs/superpowers/plans/2026-11-25-k5-kubernetes-executor.md`) — its Rulings K5-R1…R26 and its Task 12 hand-off list are what this plan tests. Consumer: R-8 (#144) renders these objects in the chart (K6-R8).

## Global Constraints

- **Worktree:** `.claude/worktrees/k6-kueue-kind`, branch `feat/k6-kueue-kind`. Task 0 rebases it onto `origin/main` after K-5 (#13) merges. `npm install` at the worktree root; `uv sync --extra dev` in `services/pipeline/`.
- **No local cluster.** `kind` is not installed on the development machine and Docker is lead-only, so the implementer **cannot run the kind leg**. What the implementer runs: `npm run verify`; `uv run pytest -q` and `uv run ruff check .` in `services/pipeline/` (the kind modules skip there); `scripts/check-kueue.sh` (kubeconform against Kueue's CRD schemas — Task 2 says how to get the binary); `bash -n` and `shellcheck` (if installed) on the three scripts. **CI on the PR is the real gate**, and an iterate-on-CI loop is expected; the lead runs it (Task 10).
- **Gates:** pipeline tasks `uv run pytest -q` + `uv run ruff check .` (from `services/pipeline/`); app test changes `npm run verify` (root). Teammates never push, never run Docker, kind or a cluster.
- **Names (fixed by K-5 and the spec — do not invent others):** runs namespace `stac-higher-runs`; pipeline ServiceAccount `stac-higher-pipeline` in namespace `stac-higher` (the RoleBinding subject in `infra/kubernetes/pipeline-rolebinding.yaml`); run ServiceAccount `stac-higher-run`; LocalQueue and ClusterQueue `process-runs`; WorkloadPriorityClasses `interactive` (1000) and `triggered` (100); ResourceFlavor `cpu-default`; kind cluster `stac-higher`; run image tag `stac-higher-process-runtime:kind`.
- **Pins (exact; one source each):**
  - `infra/kueue/kueue.env`: `KUEUE_VERSION=v0.20.8`, `KUEUE_MANIFESTS_SHA256=ecbc26a9ba2674e1829ed44efb1312c6f8dfa08de96dbbdeb91385535319290a` (sha256 of `https://github.com/kubernetes-sigs/kueue/releases/download/v0.20.8/manifests.yaml`, computed 2026-11-26).
  - `infra/kind/versions.env`: `KIND_VERSION=v0.33.0`, `KIND_SHA256=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d` (kind-linux-amd64), `KIND_NODE_IMAGE=kindest/node:v1.34.11@sha256:44e222ee2132dab25ff87301682f89eb82c7880ea3a1bf543bfe9708fd08d67d`, `KUBECTL_VERSION=v1.34.11`, `KUBECTL_SHA256=8efbb9435132a190920eb65a47a8c1ecf755ad85ab57a600c9bedbab460bb7a8` (linux/amd64).
  - kubeconform v0.8.0: linux-amd64 `9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883` (helm.yml's pin), darwin-arm64 `f84f4dfbebf4a6b0b230385fa065a39ea35e02608c2b50d025dcf64775a69d67`; its `scripts/openapi2jsonschema.py` at tag v0.8.0 `d145babfbb765004030764e1b4e518bfb7a4bd7f111691a08fa57983b81881f3`.
- **Actions:** referenced by tag, the repo's current convention (`actions/checkout@v7`, `astral-sh/setup-uv@v10.0.1`, `docker/setup-buildx-action@v4`, `docker/bake-action@v6`, `actions/upload-artifact@v7`). Issue #198 (SHA pinning) is open and undecided; this job holds only `contents: read`, which #198 ranks last. Binaries are fetched by URL and checked against a sha256 (the kubeconform precedent in `helm.yml`), not through third-party installer actions.
- **No new repo-root-context image.** The run image builds from `services/process-runtime` (its own context, through the existing bake file), so the repo-root `.dockerignore` that excludes `infra/` is not consulted and needs no change. If a later change ever builds a test image from the repo root, it must re-include exactly the path it `COPY`s (`backend-invariants` gotcha).
- **Public repo:** no account ids, hostnames, ARNs or credentials in any file; the test token is minted per CI run inside an ephemeral cluster and masked in the log.
- **Do not touch:** K-5's executor code, `infra/kubernetes/` manifests (K-5 owns them; K-6 applies them as-is — only its README may gain one sentence, Task 9), K-4's ledger and Reconciler, migrations, the chart (R-8 owns it), `components/ui/`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  ```

## Facts checked for this plan (2026-11-26)

| Fact | Source checked |
|---|---|
| Kueue v0.20.8 is the newest release (2026-11-26); v0.19.15 patches the previous minor; v0.21.0 is at rc.2 (2026-11-24) | `gh release list -R kubernetes-sigs/kueue` |
| v0.20 **no longer serves `kueue.x-k8s.io/v1beta1`**; every Kueue CRD in v0.20.8's `manifests.yaml` serves and stores `v1beta2` only | v0.20.0 release notes ("Actions Required"); the CRDs' `served`/`storage` flags in the pinned `manifests.yaml` |
| v0.20 turns on `UnadmittedWorkloadsObservability`: `QuotaReserved=False` now carries granular reasons (`WaitingForQuota`, `ExceedsMaxQuota`, `WaitingForPodsReady`, `Misconfigured`, `Suspended`) instead of `Pending`; `WorkloadPriorityClassDefaulting` (Beta, on) gives a Job with no class the `default` class | v0.20.0 release notes ("Feature") |
| Kueue supports Kubernetes **1.34 or newer**; install = `kubectl apply --server-side -f …/releases/download/<v>/manifests.yaml`; wait = `kubectl wait deploy/kueue-controller-manager -nkueue-system --for=condition=available --timeout=5m`; config = ConfigMap `kueue-manager-config`, key `controller_manager_config.yaml`, `apiVersion: config.kueue.x-k8s.io/v1beta2`; a config change needs a controller restart | kueue.sigs.k8s.io/docs/getting-started/installation/ |
| Config v1beta2: `waitForPodsReady` has **no `enable` field** (present = on); fields `timeout` (required), `blockAdmission`, `recoveryTimeout` (defaults to `timeout`), `requeuingStrategy{timestamp: Eviction\|Creation, backoffLimitCount (null = forever), backoffBaseSeconds (60), backoffMaxSeconds (3600)}`; past `backoffLimitCount` Kueue **deactivates** the Workload; a timeout eviction is `Evicted=True` reason `PodsReadyTimeout`; `manageJobsWithoutQueueName` defaults to false | kueue.sigs.k8s.io/docs/reference/kueue-config.v1beta2/, …/docs/tasks/manage/setup_wait_for_pods_ready/ |
| v1beta2 schemas: ClusterQueue `spec.queueingStrategy` ∈ {StrictFIFO, BestEffortFIFO}; `spec.preemption.withinClusterQueue` ∈ {Never, LowerPriority, LowerOrNewerEqualPriority} (default Never); `resourceGroups[].coveredResources/flavors[].name/resources[].name/nominalQuota`; `spec.namespaceSelector`; ResourceFlavor `spec.nodeLabels/nodeTaints/tolerations`; LocalQueue `spec.clusterQueue`; WorkloadPriorityClass has top-level `value`, `description` (no `spec`); Workload `spec.active` (default true) | the pinned `manifests.yaml` CRDs; a draft of Task 2's manifests passed kubeconform `-strict` against them, and a misspelt field, a bad enum and a `v1beta1` object each failed |
| A disabled integration gets a **no-op webhook**, so restricting `integrations.frameworks` to `batch/job` does not break pod creation through the still-registered `mpod.kb.io` webhook | Kueue source `pkg/controller/jobframework/setup.go` at v0.20.8 (`WithNoopWebhook(true)`) |
| Labels: `kueue.x-k8s.io/job-uid` on the Workload (owner Job's uid); `kueue.x-k8s.io/queue-name` and `kueue.x-k8s.io/priority-class` on the Job | kueue.sigs.k8s.io/docs/reference/labels-and-annotations/ |
| Kueue's documented enforcement pattern: a ValidatingAdmissionPolicy on `batch/v1 jobs` CREATE/UPDATE requiring a non-empty `kueue.x-k8s.io/queue-name`, bound to the managed namespaces | kueue.sigs.k8s.io/docs/tasks/manage/enforce_job_management/setup_job_admission_policy/ |
| ValidatingAdmissionPolicy is **GA since Kubernetes 1.30** (`admissionregistration.k8s.io/v1`); `validations[].reason` ∈ {Unauthorized, Forbidden, Invalid, RequestEntityTooLarge}, unset = Invalid; `failurePolicy` defaults to Fail; bindings take `matchResources.namespaceSelector` | kubernetes.io/docs/reference/access-authn-authz/validating-admission-policy/ |
| kind v0.33.0 (2026-08-26) ships node images v1.37.0 (default), v1.36.4, v1.35.8, **v1.34.11** (digests above; "you must use the @sha256 digest") | `gh release view v0.33.0 -R kubernetes-sigs/kind` |
| Kueue's controller requests 500m CPU / 512Mi; its image pulls `Always` | the pinned `manifests.yaml` Deployment |

## What K-6 relies on from K-5 (verify each in Task 0)

K-5 is being implemented against a fake API server. Each point below is a name, value or behaviour this plan's tests are written against. Task 0 checks every one in the merged code; a difference is adapted in the task that consumes it and listed in the PR body.

| # | Contract point (K-5 plan reference) | K-6 use |
|---|---|---|
| C1 | Settings `PROCESS_EXECUTOR=kubernetes`, `PROCESS_K8S_API_URL` (https; http only to loopback), `PROCESS_K8S_TOKEN_FILE`, `PROCESS_K8S_CA_FILE`, `PROCESS_K8S_NAMESPACE` (`stac-higher-runs`), `PROCESS_K8S_RUN_SERVICE_ACCOUNT`, `PROCESS_K8S_DEFAULT_QUEUE` (`process-runs`), read by `Settings.from_env` (Task 1) | `_kind.settings()` builds the executor from the CI env |
| C2 | `build_executor(settings)` in `process/factory.py` returns a `KubernetesExecutor` and does no I/O (Task 8, K5-R18) | the harness builds the executor exactly as the worker does |
| C3 | `KubeApi` (`process/kube_api.py`) with `from_settings`, `get`, `list`, `create`, `patch`, `delete`, `get_raw`; `KubeApiError(ExecutorUnavailable)` carrying `.status` and `.reason`; token file re-read per request (Task 3, K5-R1) | SelfSubjectReview / RulesReview, raw RBAC-denial probes, the token-rotation test |
| C4 | `infra/kubernetes/` = `namespace.yaml` (PSA `restricted`), `run-serviceaccount.yaml`, `pipeline-role.yaml`, `pipeline-rolebinding.yaml` (subject `stac-higher-pipeline` in `stac-higher`); Role rules exactly K5-R10's five (Task 2) | `kind-up.sh` applies the directory as-is; the rules-review test compares against `pipeline-role.yaml` |
| C5 | Job labels `kueue.x-k8s.io/queue-name` = profile `backend.kubernetes.queue` or the default queue, `kueue.x-k8s.io/priority-class` = `spec.priority`; the pod template carries **no** queue label; the executor never sets `spec.suspend` (Global Constraints, Task 4) | Kueue admits via the Job integration only; the VAP passes K-5's Jobs |
| C6 | Job name `stac-run-{run_id}-{hex6}`, handle = Job name, `parse_job_name` (K5-R2) | admin lookups by handle |
| C7 | Pod `activeDeadlineSeconds = timeout_seconds`, Job `= timeout + 600`, `terminationGracePeriodSeconds: 10` (K5-R4) | the timeout test |
| C8 | `cancel()`: a started pod gets `activeDeadlineSeconds: 1` (merge-patch), a never-started Job is foreground-deleted (K5-R5) | the cancel tests |
| C9 | `KUEUE_API_VERSION = "v1beta2"`; Workloads found by label `kueue.x-k8s.io/job-uid` (Task 4) | must match what v0.20.8 serves |
| C10 | `status()` table rows 1–19 (Task 6), in particular: row 1 detail `"the run's Job no longer exists"`; row 3 `lost` with `DISRUPTION_EXIT`; row 4 `timed_out=True`; row 5 `oom_error(memory_mb)`; row 10 `"Kueue deactivated the run: …"`; row 11 `"preempted: …"` / `"evicted (<reason>): …"`; row 12 = the `QuotaReserved` message; row 15 `"waiting for a node: …"` | assertions (by constant where K-5 exports one — Ask A5) |
| C11 | `watch()`: RESYNC first; Job + Workload watches; `WATCH_TIMEOUT_SECONDS` / `WATCH_IDLE_SECONDS` module constants read at call time; resume on a normal close; `RunEvent(run_id, handle_id=<job>, kind)` (Task 7, K5-R21) | the watch tests monkeypatch the server timeout |
| C12 | `logs()` reads container `run` with `limitBytes`; `b""` + WARNING when unavailable, never raises (Task 5, K5-R26) | log-survival assertions |
| C13 | `reap()` foreground-deletes the Job and deletes the env Secret (and `-pull`), idempotent; `list_launched()` raises `ExecutorUnavailable` on any API error; `probe()` lists `limit=1` (Task 5) | lifecycle test, broken-URL test |
| C14 | A 422 on the Job create raises `RunRejected` (a `HardwareUnavailable`) naming the server's message; 403/5xx raise `ExecutorUnavailable` (K5-R20) | the VAP and unknown-priority tests |
| C15 | No `imagePullPolicy` set (or `IfNotPresent`), and `rewrite_image` leaves a host-less reference unchanged (Task 4, K5-R12) | `stac-higher-process-runtime:kind` runs from the `kind load`ed copy |
| C16 | Pod: uid/gid 10001, read-only root, `emptyDir {medium: Memory}` at `/tmp` and `/home/runner` for a platform process image, `serviceAccountName: stac-higher-run`, no token (K5-R13, K5-R14) | PSA `restricted` must admit it; the runtime image must run under it |
| C17 | Secret `immutable`, owned by the Job, carries the whole `RunSpec.env` (K5-R3) | the env reaches the pod through `envFrom`; nothing of it is in the Job |
| C18 | K-4's `Reconciler`, `FakeProcessRepo` (`tests/_process_fake.py`: `enqueue_run`, `claim_run(run_id, now, backend=…)`, `record_submission(…, backend=…)`, `get_submitted_run`, `_row`) and `collect_run_log` are unchanged by K-5 | the preemption test drives the real Reconciler |

## Asks of K-5 (cheaper while K-5 is in progress)

- **A1 · Do not key on `QuotaReserved` reason `Pending`.** Kueue v0.20 reports `WaitingForQuota` / `ExceedsMaxQuota` / `WaitingForPodsReady` / `Misconfigured` / `Suspended` (facts table). K-5's row 12 should read any `QuotaReserved=False` as `pending_capacity` with its message, and the fake should emit `WaitingForQuota`. Spec §2's "reason `Pending`" is v0.19 wording.
- **A2 · Pin Kueue v0.20.8, not v0.19.2** (K6-R1) in K-5's Task 0 check and docs. `KUEUE_API_VERSION = "v1beta2"` is unchanged — v0.20 serves nothing else.
- **A3 · Read `WATCH_TIMEOUT_SECONDS` and `WATCH_IDLE_SECONDS` from the module at call time** (not as default-argument values), so K-6 can shorten the server-side timeout with `monkeypatch`.
- **A4 · Leave `imagePullPolicy` unset** (or `IfNotPresent`). `Always` would make kind try to pull the `kind load`ed image from Docker Hub.
- **A5 · Export the detail texts the tests assert as module constants** in `kubernetes_executor.py`: `JOB_GONE_DETAIL`, `JOB_DELETING_DETAIL`, `PREEMPTED_PREFIX = "preempted: "`, `WAITING_FOR_NODE_PREFIX = "waiting for a node: "`, `DEACTIVATED_PREFIX = "Kueue deactivated the run: "` (beside the planned `DISRUPTION_EXIT`).
- **A6 · A disrupted pod may be gone before `status()` looks.** On a real server an evicted pod is deleted once the Job controller drops its tracking finalizer, so row 3 (which reads the pod's `DisruptionTarget`) can miss it and row 7 then spends an attempt. Cheapest robust form: add `podFailurePolicy: {rules: [{action: FailJob, onPodConditions: [{type: DisruptionTarget}]}]}` to the Job (valid with `restartPolicy: Never`) and map a Job `Failed` condition with reason `PodFailurePolicy` to `lost` even without a pod. This contradicts K-5's "`podFailurePolicy` unset" constraint only in form: the rule fails the Job exactly as `backoffLimit: 0` already does, it just names why. If K-5 declines, K-6's eviction test (Task 7) decides.

## Rulings

Each: the decision — why — the cost if it is wrong.

- **K6-R1 · Kueue v0.20.8, not the spec's v0.19.2.** The 0.19 line stops being patched when 0.21 ships (rc.2 is out); 0.20 drops `v1beta1` (K-5 is already `v1beta2`-only) and supports Kubernetes ≥ 1.34, the floor the chart already renders against. One pin, `infra/kueue/kueue.env`, read by the scripts and (later) R-8's drift test. Task 0 takes a newer 0.20 patch if one exists, updating the sha256; a new minor needs its `.0` notes read and a plan amendment. *Cost:* v0.20's changed `QuotaReserved` reasons and priority defaulting (A1); covered by the kind tests.
- **K6-R2 · kind runs Kubernetes v1.34.11 — the supported floor — on `ubuntu-24.04`.** 1.34 matches `scripts/check-helm.sh` (`kube=1.34.0`) and helm.yml's kubeconform, is Kueue's minimum, and has ValidatingAdmissionPolicy GA (since 1.30). kubectl is pinned to the same patch to stay inside the version-skew policy (the runner's preinstalled kubectl is newer). The runner is pinned like the e2e job: kind v0.33.0's behaviour on the `ubuntu-latest` image (26.04) is unverified, and an image change must not silently change the cluster under the gate. *Cost:* a regression only on newer Kubernetes goes unseen; a `node-image` matrix is a one-line addition later.
- **K6-R3 · Two identities, kept apart.** The executor — every call under test — authenticates with a bound token for `stac-higher-pipeline` (`kubectl create token`, 2 h), so least privilege is proven against the real authorizer: a test asserts the identity (`SelfSubjectReview`) and that the identity's rules in `stac-higher-runs` equal `pipeline-role.yaml` exactly (`SelfSubjectRulesReview`), plus denials of named things the executor must not do. The harness uses the kind admin kubeconfig, only through `kubectl`, only in `tests/_kind.py`, only for fault injection (delete a Job, evict a pod) and observation (pod state, Workload conditions, events). *Cost:* the harness can do anything; mitigated by it never constructing an executor or `KubeApi`.
- **K6-R4 · The tests run on the runner, not in a pod.** They reach `https://127.0.0.1:6443` (kind's published API port, `apiServerAddress` pinned so the serving certificate's SANs include it) with the cluster CA file. This exercises K-5's real TLS, bearer and watch code; what differs in-cluster is only where the token/CA live (K-5's defaults are the projected mount) and the `KUBERNETES_SERVICE_HOST` URL derivation, both unit-tested by K-5. *Why not an in-cluster pipeline pod:* it needs the pipeline image built and loaded (minutes), Postgres, and test output scraped from a pod. *Cost:* the in-cluster network path and the pipeline pod's own NetworkPolicy are untested until R-8/R-11 (recorded, Task 9).
- **K6-R5 · The run image is the real platform runtime, built and `kind load`ed — no registry.** `docker/bake-action` builds the existing bake target `runtime` (context `services/process-runtime`) with `cache-from type=gha,scope=process-runtime` (what `containers.yml` writes) and **no `cache-to`** (this job never races the image job's cache), loads it as `stac-higher-process-runtime:kind`, and `kind load docker-image` copies it onto the node. A tag that is not `latest` defaults to `IfNotPresent`, so nothing is pulled (C15, A4). *Why:* proves the platform image itself runs as uid 10001 on a read-only root under PSA `restricted` (C16) — the thing most likely to differ from Docker. *Cost:* 1–3 min per run warm, ~5 cold; the stactools variant and the scanner image are not exercised (Task 9 records it).
- **K6-R6 · Kueue install = pinned manifests by server-side apply, our configuration swapped in, controller restarted.** `kind-up.sh` downloads `manifests.yaml` for `KUEUE_VERSION`, refuses it unless its sha256 matches, `kubectl apply --server-side`s it, replaces `kueue-manager-config` with `infra/kueue/config/kind.yaml` (server-side apply, `--force-conflicts`, field manager `stac-higher`), restarts the controller and waits for `rollout status`; then applies the objects in a retry loop (12 × 5 s) because Kueue's webhooks answer a little after the Deployment reports available, and waits for ClusterQueue and LocalQueue `Active`. *Cost:* ~30 s for the restart; a Kueue release whose manifests rename the ConfigMap fails loudly here.
- **K6-R7 · One controller configuration, a kind variant that differs only in waitForPodsReady timing.** `infra/kueue/config/production.yaml` (the deployable one) is Kueue v0.20.8's default configuration with four changes: `manageJobsWithoutQueueName: false` (explicit), `integrations.frameworks: ["batch/job"]` (Kueue manages only what the platform submits; the other integrations get no-op webhooks — facts table), and `waitForPodsReady: {timeout: 10m, blockAdmission: false, requeuingStrategy: {timestamp: Eviction, backoffLimitCount: 3, backoffBaseSeconds: 60, backoffMaxSeconds: 3600}}`. 10 m covers a GPU node from zero (spec §7.3: 2–5 min) plus a multi-GB CUDA image pull; three requeues then deactivation feeds K5-R8 (`exited`, attempt spent) so a run that can never get a node reaches `dead`. `infra/kueue/config/kind.yaml` equals it except `waitForPodsReady.timeout: 30s`, `backoffLimitCount: 1`, `backoffBaseSeconds: 5`, `backoffMaxSeconds: 10`, so the eviction → requeue → deactivation path fits in a CI test; a static test pins the difference to exactly those four keys. *Cost:* production timing is never exercised in CI (K-9 measures it); a run admitted but stuck longer than 10 m without a node is evicted (I-92's safety net, intended).
- **K6-R8 · `common/` is deployment-independent, `kind/` is this cluster's capacity; plain files, no kustomize — so R-8 reuses rather than re-authors.** `infra/kueue/common/` holds the WorkloadPriorityClasses, the LocalQueue and the admission policy + binding: identical on every deployment. `infra/kueue/kind/` holds the ResourceFlavor `cpu-default` and the ClusterQueue with kind's quota. R-8 (#144, "Reuse #14's manifests; do not re-author them") renders `common/` as chart templates with only the namespace parameterised and a drift test that its default render equals these files object for object (K5-R23's precedent: Helm cannot read outside the chart), and renders flavors + ClusterQueue from values (quota = NodePool limits) with a drift test that a kind-equivalent values set renders `infra/kueue/kind/` exactly. Kueue itself is a cluster prerequisite pinned by `kueue.env` + `config/production.yaml`, which the cluster installer consumes verbatim (ADR 0024: the cluster is deployed from the private repo). *Cost:* two copies until R-8's drift tests exist; one-object-per-concern files are slightly more files.
- **K6-R9 · The admission policy refuses a Job without a queue AND a Job without a known priority, with reason `Invalid`.** Two validations on `batch/v1 jobs` CREATE+UPDATE, `failurePolicy: Fail`, bound to the runs namespace by `kubernetes.io/metadata.name` (no new namespace label, so K-5's `namespace.yaml` is untouched): a non-empty `kueue.x-k8s.io/queue-name`, and `kueue.x-k8s.io/priority-class` ∈ {`interactive`, `triggered`}. The priority check exists because v0.20 silently gives a class-less Job the `default` class. `reason: Invalid` makes the API answer 422, which K5-R20 maps to `RunRejected` → the run dies loudly with `process_failed`, instead of 403 → `ExecutorUnavailable` → a silent requeue loop. *Cost:* a third priority class needs a policy edit; per-group LocalQueues (I-96) need none.
- **K6-R10 · The ClusterQueue admits only the runs namespace.** `namespaceSelector` matches `kubernetes.io/metadata.name: stac-higher-runs` rather than `{}`, so a LocalQueue someone creates elsewhere cannot spend run quota. Preemption `withinClusterQueue: LowerPriority`, `reclaimWithinCohort: Never` (no cohort yet), `queueingStrategy: BestEffortFIFO`. *Cost:* per-group namespaces (I-96) widen the selector.
- **K6-R11 · kind's quota is 1 CPU and 4 Gi; every kind profile fits inside it alone.** `standard` in `kind.json` has `cpu.max: 1` — a profile whose maximum exceeds the quota would make a valid run pend forever (Kueue marks it inadmissible). Memory is deliberately not the binding resource. A static test pins "every kind profile's maxima ≤ the ClusterQueue's quota" and "every profile's `backend.kubernetes.queue` names the LocalQueue". *Cost:* none beyond CI.
- **K6-R12 · Whether a stopped pod keeps its log is decided by test; the fallback is named now.** K5-R4/R5 assume the kubelet keeps a deadline-killed container and `pods/log` serves it. Two tests (cancel, timeout) assert the marker the run printed before the stop is in `logs()` after it. On failure the test message carries a diagnosis from `_kind.diagnose_log_loss`: **(a)** the pod still exists and its log is empty or refused → the assumption is false; the fallback is **read before stop**: K-4's cancel row collects the log before calling `cancel()` for a backend whose new `Executor.logs_survive_stop: ClassVar[bool]` is `False`, and K-5's `status()` enforces the run's timeout on read (Docker's model: `exited(timed_out)` once `now > startTime + timeout` while the pod still runs, so the Reconciler collects a live log; the pod deadline becomes `timeout + 60` as a backstop). **(b)** the pod is gone → not the assumption but a K-5 bug (something deleted it); fix that. Either is a `fix/` PR after K-6 merges or on top of it, raised on epic #2; K-6's test is the arbiter and stays. *Cost:* until decided, a cancelled or timed-out run on Kubernetes may record an empty log.
- **K6-R13 · A real `410 Gone` is not provoked on kind.** The apiserver's watch cache, not etcd compaction, answers watches, and nothing in a kind cluster compacts it on demand; the fake (K-5 Task 7) owns the 410 path. K-6 proves the other half on a real server: with `WATCH_TIMEOUT_SECONDS` shortened to 3, the server closes the stream, `watch()` resumes from its last resourceVersion **without** raising, and a later submission still produces an event. Recorded in ISSUES. *Cost:* a real-server 410 quirk would first show in production as a watcher reconnect (the tick is the backstop either way).
- **K6-R14 · A path-filtered workflow `kind.yml`, plus a weekly schedule; not a required check.** Path filters keep a 12–15-minute job off unrelated PRs (helm.yml/infra.yml precedent); a skipped path-filtered workflow reports nothing, so it must not be a required status check. The schedule (Mondays) catches upstream drift (a node image or release asset moved). Jobs: `static` (kubeconform, ~1 min) and `kind` (`needs: static`). *Cost:* a change outside the filter that breaks the leg is caught by the weekly run, not the PR.
- **K6-R15 · Static validation is kubeconform against Kueue's own CRDs.** `scripts/check-kueue.sh` downloads the pinned `manifests.yaml` (sha256-checked), generates JSON schemas with kubeconform's `openapi2jsonschema.py` (v0.8.0, sha256-checked), **deletes the `v1beta1` schemas** (v0.20 does not serve them, so a `v1beta1` manifest must fail), and runs `kubeconform -strict -kubernetes-version 1.34.0` over `infra/kueue/common`, `infra/kueue/kind` and `infra/kubernetes`. The datreeio CRD catalog helm.yml uses is not trusted to carry Kueue v0.20's schemas. CEL in the policy cannot be checked statically; the kind leg is its test. *Cost:* the script needs network (as helm.yml's does).
- **K6-R16 · The suite is gated by `KIND_INTEGRATION=1` and can never skip silently in CI.** Unset (dev, the pipeline job) → the two modules skip at collection. Set, with any of `PROCESS_K8S_API_URL`, `PROCESS_K8S_TOKEN_FILE`, `PROCESS_K8S_CA_FILE`, `KIND_ADMIN_KUBECONFIG`, `KIND_RUNTIME_IMAGE` missing → collection **fails** naming them. The CI step additionally reads its JUnit XML and fails if anything skipped or fewer than the expected number of tests ran.
- **K6-R17 · Serial tests, an autouse cleanup, explicit deadlines.** All tests share one 1-CPU quota, so each starts and ends with the harness deleting every Job (foreground) and run Secret in the namespace and waiting until no Workload or pod remains. No `pytest-timeout` (no new dependency): every wait in `_kind.py` has its own deadline (default 120 s) and fails with the last observed state; the CI step has `timeout-minutes: 15`, the job 30.
- **K6-R18 · Preemption is proven through K-4's real Reconciler and the in-memory ledger.** "Returns to waiting without spending an attempt" is a ledger claim; the test records the preempted run's submission in `FakeProcessRepo` with `backend="kubernetes"`, reconciles it with the real `Reconciler` while Kueue has evicted it, and asserts the row stays `running` with its attempt count unchanged, then `succeeded` after readmission with the same count. Postgres adds nothing here — K-4's DB integration tests pin the SQL. *Cost:* Reconciler + Postgres + a real cluster together waits for K-9.

## Review Focus

- **Least privilege on a real authorizer:** the executor's calls use only the pipeline token — Task 5 `test_the_tests_run_as_the_pipeline_service_account`, `test_the_pipeline_identity_has_exactly_the_roles_rules` and Task 7 `test_the_pipeline_cannot_do_what_it_must_not`. A test that "passes" because it used the admin kubeconfig is the failure mode to look for.
- **The log after a stop (K6-R12):** Task 6 `test_cancel_of_a_running_run_keeps_its_log` and `test_a_run_past_its_timeout_is_killed_and_keeps_its_log` — their failure messages must say which fallback applies.
- **No attempt spent on preemption:** Task 7 `test_interactive_preempts_triggered_without_spending_an_attempt` drives the real Reconciler.
- **The admission policy fails closed and loud:** Task 7 `test_a_job_without_a_queue_or_priority_is_rejected` and `test_a_run_with_an_unknown_priority_dies_rather_than_requeues` (422 → `RunRejected`).
- **Nothing skips in CI:** Task 8's JUnit check, and K6-R16's collection failure.
- **Reuse by R-8:** `common/` is namespace-parameterisable as-is; the kind/production config diff is pinned (Task 1).

---

## Tasks

### Task 0: Pre-flight

- [ ] `gh pr list --state merged --search "K-5 in:title"` shows K-5 (#13) merged. If not: STOP — Tasks 1–3 and 8's `static` job do not depend on K-5's code and may proceed only with the lead's say-so; Tasks 4–7 need it.
- [ ] `git fetch origin && git rebase origin/main`; `npm install` at the root; `uv sync --extra dev` in `services/pipeline/`; all gates green before any change. `uv run python -c "import yaml"` succeeds (PyYAML for the static tests; K-5 Task 2 relies on it too).
- [ ] Walk contract points C1–C18 against the merged code: `grep -n` each name in `services/pipeline/src/pipeline/config.py`, `process/factory.py`, `process/kube_api.py`, `process/kubernetes_executor.py`, `process/kubernetes_manifests.py`, `infra/kubernetes/*.yaml`, `tests/_process_fake.py`. For each difference, note it for the PR body and adapt the consuming task before starting it. Check Asks A1–A6: for each one K-5 did not take, the consuming test keeps the plan's assertion and its failure is the evidence (A6 → Task 7's eviction test; A1 → Task 7's capacity test; A3 → Task 6's resume test shortens the server timeout through whatever seam K-5 exposes; A4/A5 → adapt the assertion text).
- [ ] Versions: `gh release list -R kubernetes-sigs/kueue -L 5`. If a newer **v0.20.x** patch exists, use it: recompute `KUEUE_MANIFESTS_SHA256` (`curl -sSfL …/manifests.yaml | sha256sum`), read its release notes for anything under "Actions Required", and say so in the PR. A newer minor is out of scope (K6-R1). `gh release view -R kubernetes-sigs/kind --json tagName -q .tagName` — keep v0.33.0 unless it was withdrawn.
- [ ] Next free ISSUES number: read `docs/ISSUES.md` now (K-5 adds entries): `grep -o '^### I-[0-9]*' docs/ISSUES.md | sort -t- -k2 -n | tail -1`. Task 9 numbers from there.
- [ ] `gh issue edit 14 --add-assignee @me --remove-label blocked`.

### Task 1: Kueue pin and controller configuration

**Files:**
- Create: `infra/kueue/kueue.env`
- Create: `infra/kueue/config/production.yaml`
- Create: `infra/kueue/config/kind.yaml`
- Create: `services/pipeline/tests/test_kueue_manifests.py` (static; runs in the ordinary pipeline job)

**Interfaces:**
- Produces `infra/kueue/kueue.env` (sourced by bash, parsed by tests — `KEY=value` lines and `#` comments only):
  ```bash
  # The Kueue release every deployment installs (K-6, K6-R1). One source:
  # scripts/kind-up.sh and scripts/check-kueue.sh read it; R-8's drift test and
  # the cluster installer must too. Bump both lines together.
  KUEUE_VERSION=v0.20.8
  KUEUE_MANIFESTS_SHA256=ecbc26a9ba2674e1829ed44efb1312c6f8dfa08de96dbbdeb91385535319290a
  ```
- Produces `infra/kueue/config/production.yaml` — a complete `Configuration` (it replaces the ConfigMap key, so every default the controller needs must be present):
  ```yaml
  # Kueue controller configuration for every stac-higher cluster (K-6, K6-R7).
  # Kueue v0.20.8's shipped default plus four changes, each marked "K-6".
  # Installed as ConfigMap kueue-manager-config, key controller_manager_config.yaml,
  # in kueue-system; a change needs `kubectl rollout restart` of the controller.
  apiVersion: config.kueue.x-k8s.io/v1beta2
  kind: Configuration
  health:
    healthProbeBindAddress: :8081
  metrics:
    bindAddress: :8443
  webhook:
    port: 9443
  leaderElection:
    leaderElect: true
    resourceName: c1f6bfd2.kueue.x-k8s.io
  controller:
    groupKindConcurrency:
      Job.batch: 5
      Pod: 5
      Workload.kueue.x-k8s.io: 10
      LocalQueue.kueue.x-k8s.io: 5
      Cohort.kueue.x-k8s.io: 1
      ClusterQueue.kueue.x-k8s.io: 5
      ResourceFlavor.kueue.x-k8s.io: 1
  clientConnection:
    qps: 300
    burst: 500
  # K-6: an admitted run whose pod is not ready in 10 minutes (no node came —
  # spec §7.3, I-92) is evicted and requeued; after three requeues Kueue
  # deactivates it and the executor reports it exited (K5-R8).
  waitForPodsReady:
    timeout: 10m
    blockAdmission: false
    requeuingStrategy:
      timestamp: Eviction
      backoffLimitCount: 3
      backoffBaseSeconds: 60
      backoffMaxSeconds: 3600
  # K-6: Jobs without a queue label are not Kueue's; the runs namespace's
  # admission policy refuses them instead (infra/kueue/common/admission-policy.yaml).
  manageJobsWithoutQueueName: false
  # K-6: Kueue manages only what the platform submits. Disabled integrations
  # keep a no-op webhook (jobframework/setup.go), so pods still create.
  integrations:
    frameworks:
      - "batch/job"
  ```
- Produces `infra/kueue/config/kind.yaml`: byte-for-byte the same document except the header comment ("kind CI variant — differs from production.yaml only in waitForPodsReady timing, pinned by test_kueue_manifests.py") and `timeout: 30s`, `backoffLimitCount: 1`, `backoffBaseSeconds: 5`, `backoffMaxSeconds: 10`.

- [ ] **Step 1: Failing tests** (`test_kueue_manifests.py`; module docstring: "K-6: static checks of infra/kueue and infra/hardware-profiles/kind.json — no cluster"; a helper `_load(path) -> list[dict]` = `yaml.safe_load_all` without `None`s; `REPO = Path(__file__).resolve().parents[3]`):
  - `test_kueue_env_pins_one_release` — parses `kueue.env` into a dict: exactly the keys `KUEUE_VERSION`, `KUEUE_MANIFESTS_SHA256`; version matches `^v0\.20\.\d+$` (K6-R1: changing the minor is a plan change); sha is 64 hex.
  - `test_the_production_config_is_what_k6_decided` — `apiVersion == "config.kueue.x-k8s.io/v1beta2"`, `kind == "Configuration"`; `manageJobsWithoutQueueName is False`; `integrations.frameworks == ["batch/job"]`; `waitForPodsReady` has `timeout: "10m"`, `blockAdmission: False`, `requeuingStrategy == {"timestamp": "Eviction", "backoffLimitCount": 3, "backoffBaseSeconds": 60, "backoffMaxSeconds": 3600}`; no `enable` key anywhere under `waitForPodsReady` (v1beta2 has none — facts table).
  - `test_the_kind_config_differs_only_in_wait_for_pods_ready_timing` — load both, delete from each the four paths `waitForPodsReady.timeout`, `…requeuingStrategy.backoffLimitCount`, `…backoffBaseSeconds`, `…backoffMaxSeconds`; the remainders are equal; kind's values are `"30s"`, `1`, `5`, `10`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_kueue_manifests.py -q`.
- [ ] **Step 3: Write the three files** exactly as above.
- [ ] **Step 4: Gates and commit.**
  ```bash
  git commit -m "K-6: Kueue v0.20.8 pin and controller configuration (waitForPodsReady, batch/job only, no queue-less Jobs); kind timing variant"
  ```

### Task 2: The Kueue objects and their static validation

**Files:**
- Create: `infra/kueue/common/workloadpriorityclasses.yaml`
- Create: `infra/kueue/common/localqueue.yaml`
- Create: `infra/kueue/common/admission-policy.yaml`
- Create: `infra/kueue/kind/resourceflavors.yaml`
- Create: `infra/kueue/kind/clusterqueue.yaml`
- Create: `scripts/check-kueue.sh`
- Test: `services/pipeline/tests/test_kueue_manifests.py` (append)

**Interfaces:**
- Produces (each file starts with a comment naming K-6, the spec section and "R-8 renders this from the chart with a drift test — edit both"):
  ```yaml
  # common/workloadpriorityclasses.yaml — spec §7.1
  apiVersion: kueue.x-k8s.io/v1beta2
  kind: WorkloadPriorityClass
  metadata:
    name: interactive
  value: 1000
  description: "Operator test runs from the UI; may preempt triggered runs in the same ClusterQueue."
  ---
  apiVersion: kueue.x-k8s.io/v1beta2
  kind: WorkloadPriorityClass
  metadata:
    name: triggered
  value: 100
  description: "Runs started by a source, a schedule or an association."
  ```
  ```yaml
  # common/localqueue.yaml — spec §7.1
  apiVersion: kueue.x-k8s.io/v1beta2
  kind: LocalQueue
  metadata:
    name: process-runs
    namespace: stac-higher-runs
  spec:
    clusterQueue: process-runs
  ```
  ```yaml
  # common/admission-policy.yaml — spec §7.1, K6-R9
  apiVersion: admissionregistration.k8s.io/v1
  kind: ValidatingAdmissionPolicy
  metadata:
    name: stac-higher-runs-require-kueue
  spec:
    failurePolicy: Fail
    matchConstraints:
      resourceRules:
        - apiGroups: ["batch"]
          apiVersions: ["v1"]
          operations: ["CREATE", "UPDATE"]
          resources: ["jobs"]
    validations:
      - expression: >-
          has(object.metadata.labels) &&
          'kueue.x-k8s.io/queue-name' in object.metadata.labels &&
          object.metadata.labels['kueue.x-k8s.io/queue-name'] != ''
        message: "Jobs in this namespace must carry a non-empty kueue.x-k8s.io/queue-name label (Kueue admits every process run)."
        reason: Invalid
      - expression: >-
          has(object.metadata.labels) &&
          'kueue.x-k8s.io/priority-class' in object.metadata.labels &&
          object.metadata.labels['kueue.x-k8s.io/priority-class'] in ['interactive', 'triggered']
        message: "Jobs in this namespace must carry kueue.x-k8s.io/priority-class 'interactive' or 'triggered'."
        reason: Invalid
  ---
  apiVersion: admissionregistration.k8s.io/v1
  kind: ValidatingAdmissionPolicyBinding
  metadata:
    name: stac-higher-runs-require-kueue
  spec:
    policyName: stac-higher-runs-require-kueue
    validationActions: [Deny]
    matchResources:
      namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: stac-higher-runs
  ```
  ```yaml
  # kind/resourceflavors.yaml — one flavor per profile family in kind.json (CPU only):
  # no nodeLabels, so any node; GPU flavors arrive with K-8's eks.json.
  apiVersion: kueue.x-k8s.io/v1beta2
  kind: ResourceFlavor
  metadata:
    name: cpu-default
  spec: {}
  ```
  ```yaml
  # kind/clusterqueue.yaml — K6-R10, K6-R11: one CPU in total, so a second run
  # waits for capacity (pending_capacity is exercised, spec §8).
  apiVersion: kueue.x-k8s.io/v1beta2
  kind: ClusterQueue
  metadata:
    name: process-runs
  spec:
    namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: stac-higher-runs
    queueingStrategy: BestEffortFIFO
    preemption:
      withinClusterQueue: LowerPriority
      reclaimWithinCohort: Never
    resourceGroups:
      - coveredResources: ["cpu", "memory"]
        flavors:
          - name: cpu-default
            resources:
              - name: cpu
                nominalQuota: "1"
              - name: memory
                nominalQuota: 4Gi
  ```
  These five objects (plus the binding) were checked with kubeconform `-strict` against v0.20.8's CRDs while writing this plan.
- Produces `scripts/check-kueue.sh` (`#!/usr/bin/env bash`, `set -euo pipefail`, run from the repo root, usage `scripts/check-kueue.sh [work-dir]`, default `$(mktemp -d)`):
  1. `source infra/kueue/kueue.env`; `curl -sSfL -o "$work/manifests.yaml" "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"`; `echo "${KUEUE_MANIFESTS_SHA256}  $work/manifests.yaml" | sha256sum -c -` (on macOS fall back to `shasum -a 256 -c -` when `sha256sum` is absent).
  2. Fetch `https://raw.githubusercontent.com/yannh/kubeconform/v0.8.0/scripts/openapi2jsonschema.py` to `$work/` and check `d145babfbb765004030764e1b4e518bfb7a4bd7f111691a08fa57983b81881f3`.
  3. `mkdir "$work/schemas" && (cd "$work/schemas" && uv run --no-project --with pyyaml python -I ../openapi2jsonschema.py ../manifests.yaml >/dev/null)`; `rm -f "$work"/schemas/*_v1beta1.json` (K6-R15).
  4. `kubeconform -strict -summary -kubernetes-version 1.34.0 -schema-location default -schema-location "$work/schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" infra/kueue/common infra/kueue/kind infra/kubernetes`.
  The script requires `kubeconform` on `PATH` and says how to get it when missing (CI installs it as helm.yml does; locally: download `kubeconform-darwin-arm64.tar.gz` v0.8.0, check `f84f4dfbebf4a6b0b230385fa065a39ea35e02608c2b50d025dcf64775a69d67`, put the binary on `PATH`).

- [ ] **Step 1: Failing tests** (`test_kueue_manifests.py`, appended):
  - `test_every_kueue_object_is_v1beta2` — every document under `infra/kueue/common` and `infra/kueue/kind` whose `apiVersion` starts with `kueue.x-k8s.io/` has exactly `kueue.x-k8s.io/v1beta2` (and equals K-5's `kubernetes_manifests.KUEUE_API_VERSION`).
  - `test_priority_classes_are_the_executors` — names equal `set(executor.PRIORITIES)`; `interactive.value > triggered.value`; values `1000` and `100`.
  - `test_the_local_queue_is_the_executors_default` — one LocalQueue, `metadata.name == DEFAULT_PROCESS_K8S_DEFAULT_QUEUE`, `metadata.namespace == DEFAULT_PROCESS_K8S_NAMESPACE`, `spec.clusterQueue` names the ClusterQueue in `kind/clusterqueue.yaml`.
  - `test_the_admission_policy_covers_the_runs_namespace` — the binding's `policyName` equals the policy's name; `validationActions == ["Deny"]`; its namespace selector's `kubernetes.io/metadata.name == DEFAULT_PROCESS_K8S_NAMESPACE`; the policy matches `batch`/`v1`/`jobs` with operations `{"CREATE", "UPDATE"}`; `failurePolicy == "Fail"`; every validation has `reason == "Invalid"`; the two expressions name `kueue.x-k8s.io/queue-name` and `kueue.x-k8s.io/priority-class`, and the priority expression's list equals `executor.PRIORITIES` (parse the `['…', '…']` literal with a regex).
  - `test_the_cluster_queue_is_what_k6_decided` — `queueingStrategy == "BestEffortFIFO"`, `preemption.withinClusterQueue == "LowerPriority"`, `reclaimWithinCohort == "Never"`, `namespaceSelector` matches only the runs namespace; every flavor it names exists in `kind/resourceflavors.yaml`; covered resources are exactly `["cpu", "memory"]`.

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Write the five manifests and the script** (`chmod +x scripts/check-kueue.sh`).
- [ ] **Step 4: Static validation locally.** Install kubeconform as the script says, then `scripts/check-kueue.sh "$TMPDIR/check-kueue"` — `Summary: … Invalid: 0, Errors: 0`. Negative check (do not commit): change `queueingStrategy` to `BestEffortFIFOO` in a scratch copy and confirm kubeconform fails, then discard. `bash -n scripts/check-kueue.sh`; `shellcheck scripts/check-kueue.sh` if installed.
- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-6: Kueue objects — priority classes, LocalQueue, queue/priority admission policy (common); cpu-default flavor and a 1-CPU ClusterQueue (kind); kubeconform against Kueue's own CRDs"
  ```

### Task 3: `infra/hardware-profiles/kind.json`

**Files:**
- Create: `infra/hardware-profiles/kind.json`
- Test: `services/pipeline/tests/test_kueue_manifests.py` (append), `services/pipeline/tests/test_process_hardware.py` (append), `app/src/__tests__/processes-hardware.test.ts` (append)

**Interfaces:**
- Produces (the CI deployment's profile set; not copied into any image — the pipeline and app images copy only `local.json`):
  ```json
  {
    "version": 1,
    "profiles": [
      {
        "id": "standard",
        "label": "Standard",
        "description": "kind CI: one CPU of quota in total, so a second run waits for capacity.",
        "tier": "cpu",
        "accelerator": null,
        "cpu": { "min": 0.25, "max": 1, "default": 1 },
        "memory_mb": { "min": 128, "max": 1024, "default": 512 },
        "gpu_count": null,
        "max_queue_wait_seconds": 600,
        "image": null,
        "backend": {
          "kubernetes": {
            "queue": "process-runs",
            "node_selector": {},
            "tolerations": [],
            "runtime_class": null,
            "extended_resources": {}
          }
        }
      }
    ]
  }
  ```

- [ ] **Step 1: Failing tests.**
  - `test_kueue_manifests.py`:
    - `test_kind_profiles_fit_the_cluster_queue` (K6-R11) — load with `load_hardware_profiles(path)`; for every profile, `cpu.max` ≤ the ClusterQueue's `cpu` quota (parse `"1"` / `"500m"` with a three-line helper), `memory_mb.max` MiB ≤ the `memory` quota (`"4Gi"` = 4096), `gpu_count is None` (no GPU flavor on kind).
    - `test_kind_profiles_name_the_local_queue_and_existing_flavors` — `kubernetes_backend(p).queue == "process-runs"`; every profile's `node_selector` equals the `nodeLabels` (or `{}`) of some ResourceFlavor in `kind/resourceflavors.yaml` (spec §3.4: the selector must equal the flavor's labels).
  - `test_process_hardware.py`: `test_the_kind_set_parses_and_agrees_with_the_defaults` — `standard` exists, `cpu.default == DEFAULT_HARDWARE_CPU`, `gpu_count is None`, `kubernetes_backend(standard) == KubernetesBackend(queue="process-runs")`.
  - `processes-hardware.test.ts`: `it("the shipped kind set parses and agrees with the defaults")` — same shape as the existing local-set case, reading `../../../infra/hardware-profiles/kind.json`, plus `publicProfiles(set)` has no `backend`.
- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_kueue_manifests.py tests/test_process_hardware.py -q`; `npx vitest run src/__tests__/processes-hardware.test.ts` (from `app/`).
- [ ] **Step 3: Write `kind.json`.**
- [ ] **Step 4: Gates (`npm run verify`; pytest; ruff) and commit.**
  ```bash
  git commit -m "K-6: kind hardware-profile set — one CPU profile that fits a 1-CPU Kueue quota; both readers parse it"
  ```

### Task 4: The kind cluster — config, bring-up and diagnostics scripts

**Files:**
- Create: `infra/kind/versions.env`
- Create: `infra/kind/cluster.yaml`
- Create: `scripts/kind-up.sh`
- Create: `scripts/kind-diagnostics.sh`
- Test: `services/pipeline/tests/test_kueue_manifests.py` (append)

**Interfaces:**
- Produces `infra/kind/versions.env` with the five `KIND_*`/`KUBECTL_*` lines from Global Constraints and a comment (K6-R2; "the node image MUST be pinned by digest — kind's release notes").
- Produces `infra/kind/cluster.yaml` (no `image:` — `kind-up.sh` passes `--image "$KIND_NODE_IMAGE"`, so the digest lives in one file):
  ```yaml
  # kind cluster for the Kubernetes executor's CI leg (K-6). Single node.
  # The API server is published on 127.0.0.1:6443 so the tests (on the
  # runner) reach it at a fixed URL covered by its serving certificate (K6-R4).
  kind: Cluster
  apiVersion: kind.x-k8s.io/v1alpha4
  name: stac-higher
  networking:
    apiServerAddress: "127.0.0.1"
    apiServerPort: 6443
  nodes:
    - role: control-plane
  ```
- Produces `scripts/kind-up.sh <out-dir>` (`set -euo pipefail`; repo root; requires `docker`, `kind`, `kubectl`, `curl`; every `kubectl` call uses `--kubeconfig "$out/admin.kubeconfig"`):
  1. `source infra/kind/versions.env infra/kueue/kueue.env`; `mkdir -p "$out"`.
  2. `kind create cluster --config infra/kind/cluster.yaml --image "$KIND_NODE_IMAGE" --kubeconfig "$out/admin.kubeconfig" --wait 180s`.
  3. Kueue (K6-R6): download `manifests.yaml`, `sha256sum -c` against `KUEUE_MANIFESTS_SHA256` (abort on mismatch); `kubectl apply --server-side -f "$out/kueue-manifests.yaml"`; `kubectl -n kueue-system create configmap kueue-manager-config --from-file=controller_manager_config.yaml=infra/kueue/config/kind.yaml --dry-run=client -o yaml | kubectl apply --server-side --force-conflicts --field-manager=stac-higher -f -`; `kubectl -n kueue-system rollout restart deploy/kueue-controller-manager`; `kubectl -n kueue-system rollout status deploy/kueue-controller-manager --timeout=300s`.
  4. K-5's RBAC and the pipeline's identity: `kubectl create namespace stac-higher`; `kubectl -n stac-higher create serviceaccount stac-higher-pipeline`; `kubectl apply --server-side -f infra/kubernetes/` (C4; `kubectl` reads only `.yaml`/`.yml`/`.json` from a directory, so K-5's README there is ignored).
  5. Kueue objects, retried while the webhook comes up: `for i in $(seq 1 12); do kubectl apply --server-side -f infra/kueue/common/ -f infra/kueue/kind/ && break; sleep 5; done` — fail after the 12th; then `kubectl wait --for=condition=Active clusterqueue/process-runs --timeout=120s` and `kubectl -n stac-higher-runs wait --for=condition=Active localqueue/process-runs --timeout=120s`.
  6. Credentials for the tests: `kubectl -n stac-higher create token stac-higher-pipeline --duration=2h > "$out/pipeline.token"` (`chmod 600`; in CI `echo "::add-mask::$(cat …)"` when `GITHUB_ACTIONS` is set); the CA from the admin kubeconfig: `kubectl config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' | base64 -d > "$out/ca.crt"`; the URL: `kubectl config view -o jsonpath='{.clusters[0].cluster.server}'` (expected `https://127.0.0.1:6443`).
  7. Write `"$out/kind.env"`: `KIND_INTEGRATION=1`, `PROCESS_EXECUTOR=kubernetes`, `PROCESS_K8S_API_URL=<url>`, `PROCESS_K8S_TOKEN_FILE=$out/pipeline.token`, `PROCESS_K8S_CA_FILE=$out/ca.crt`, `KIND_ADMIN_KUBECONFIG=$out/admin.kubeconfig`, `KIND_RUNTIME_IMAGE=stac-higher-process-runtime:kind`, `KIND_OUT=$out`. Print a one-line summary (versions, URL) — never the token.
  The script does not build or load the run image (Task 8 does, so the build overlaps cluster creation in CI); it ends by printing the `kind load docker-image … --name stac-higher` command a local user runs.
- Produces `scripts/kind-diagnostics.sh <out-dir> <admin-kubeconfig>` (`set -uo pipefail`, **no** `-e`: every command `|| true`, so one failure does not hide the rest):
  `kind export logs "$out/kind-logs" --name stac-higher`; `kubectl get nodes -o wide`; `kubectl version`; in `stac-higher-runs`: `kubectl get jobs,pods,workloads.kueue.x-k8s.io,localqueues.kueue.x-k8s.io -o yaml`, `kubectl get events --sort-by=.lastTimestamp`, `kubectl describe pods`; cluster-scoped: `kubectl get clusterqueues,resourceflavors,workloadpriorityclasses -o yaml`, `kubectl get validatingadmissionpolicies,validatingadmissionpolicybindings -o yaml`; `kubectl -n kueue-system logs deploy/kueue-controller-manager --tail=2000`; `kubectl -n kueue-system get configmap kueue-manager-config -o yaml`. Each into its own file under `$out/`. **Never** `get secrets` — run Secrets carry run environments (K5-R3); a comment says so.

- [ ] **Step 1: Failing tests** (`test_kueue_manifests.py`): `test_kind_runs_the_charts_kubernetes_minor` — parse `infra/kind/versions.env`: `KIND_NODE_IMAGE` matches `^kindest/node:v(\d+\.\d+)\.\d+@sha256:[0-9a-f]{64}$`, its minor equals the minor of `kube=` in `scripts/check-helm.sh` (`1.34`) and `KUBECTL_VERSION`'s minor; `KIND_SHA256` and `KUBECTL_SHA256` are 64 hex; and `infra/kind/cluster.yaml` has no `image` key (the digest lives once).
- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Write the four files**; `chmod +x` both scripts.
- [ ] **Step 4: Check what can be checked without a cluster:** `bash -n scripts/kind-up.sh scripts/kind-diagnostics.sh`; `shellcheck` if installed; a reviewer reads `kind-up.sh` against K6-R6 line by line. The script's first real run is the CI leg (Task 10).
- [ ] **Step 5: Gates and commit.**
  ```bash
  git commit -m "K-6: kind cluster config and bring-up — pinned kind/node/kubectl, Kueue by server-side apply with our config, K-5 RBAC, a pipeline token; failure diagnostics without Secrets"
  ```

### Task 5: The harness and the identity tests

**Files:**
- Create: `services/pipeline/tests/_kind.py`
- Create: `services/pipeline/tests/test_integration_kind_executor.py` (identity and transport tests here; Task 6 appends the lifecycle tests)

**Interfaces:**
- Produces (tests only):
  ```python
  # tests/_kind.py — the kind leg's harness (K-6). The executor under test is
  # built ONLY from the pipeline ServiceAccount's token (K6-R3); the admin
  # kubeconfig is used ONLY through kubectl() for fault injection and
  # observation. Nothing here constructs a KubeApi or an executor from admin
  # credentials.
  ENABLED: bool                       # os.environ.get("KIND_INTEGRATION") == "1"
  REQUIRED_ENV = ("PROCESS_K8S_API_URL", "PROCESS_K8S_TOKEN_FILE", "PROCESS_K8S_CA_FILE",
                  "KIND_ADMIN_KUBECONFIG", "KIND_RUNTIME_IMAGE")
  NAMESPACE = "stac-higher-runs"
  PIPELINE_USER = "system:serviceaccount:stac-higher:stac-higher-pipeline"
  WAIT_SECONDS = 120
  MARKER = "K6-MARKER"
  REPO = Path(__file__).resolve().parents[3]

  def require() -> None                        # ENABLED and a var missing ⇒ RuntimeError naming them (K6-R16)
  skip_unless_kind: pytest.MarkDecorator       # skipif(not ENABLED, reason="KIND_INTEGRATION is not set (the kind CI leg sets it)")
  def settings(**overrides: str) -> Settings   # Settings.from_env({**os.environ, "PROCESS_EXECUTOR": "kubernetes", **overrides})
  def executor(**overrides: str) -> Executor   # build_executor(settings(**overrides)) — the worker's own path (C2)
  def api(**overrides: str) -> KubeApi         # KubeApi.from_settings(settings(**overrides)) — pipeline identity
  def kubectl(*args: str, input: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]
                                               # ["kubectl", "--kubeconfig", $KIND_ADMIN_KUBECONFIG, *args], text, 60 s timeout
  def kget(resource: str, name: str | None = None, *, namespace: str | None = NAMESPACE,
           selector: str | None = None) -> dict | None   # `get -o json`; None on NotFound
  def standard_profile() -> HardwareProfile    # "standard" from infra/hardware-profiles/kind.json
  def code_spec(code: str, *, run_id: str | None = None, timeout_seconds: int = 120,
                memory_mb: int = 256, cpu: float = 1.0, priority: str = "triggered",
                env: Mapping[str, str] | None = None,
                profile: HardwareProfile | None = None) -> RunSpec
      # image = $KIND_RUNTIME_IMAGE; env = {**env, "STAC_HIGHER_PROCESS_CODE_B64": b64(code)};
      # run_id = str(uuid4()) by default; process_id = "k6-process"; kind = "process"
  def wait_for(probe: Callable[[], T], done: Callable[[T], bool], *, what: str,
               timeout: float = WAIT_SECONDS, interval: float = 1.0) -> T
      # polls; AssertionError(f"{what}: not reached in {timeout}s; last={last!r}")
  def wait_state(ex: Executor, handle: RunHandle, phases: set[str], *,
                 seen: list[RunState] | None = None, timeout: float = WAIT_SECONDS) -> RunState
      # appends every DISTINCT (phase, detail) observed to `seen`
  def wait_log(ex: Executor, handle: RunHandle, needle: bytes, *, timeout: float = 60) -> bytes
  def pod_of(handle: RunHandle) -> dict | None # newest pod by batch.kubernetes.io/job-name (admin)
  def workload_of(handle: RunHandle) -> dict | None  # by kueue.x-k8s.io/job-uid of the Job (admin)
  def diagnose_log_loss(handle: RunHandle) -> str
      # K6-R12: "pod present (phase=…, reason=…, container=<state>) — log lost by the kubelet:
      #  take the read-before-stop fallback" | "pod gone — something deleted it: a K-5 bug, not the fallback"
  def clean_namespace() -> None
      # kubectl delete jobs --all -n NAMESPACE --cascade=foreground --wait=true --timeout=90s;
      # kubectl delete secrets -l stac-higher.run-id -n NAMESPACE --wait=true;
      # then wait until no Workload and no pod remains in NAMESPACE (K6-R17)
  ```
  Each integration module starts with:
  ```python
  pytestmark = _kind.skip_unless_kind
  _kind.require()                      # at import: a half-configured CI run fails, never skips

  @pytest.fixture(autouse=True)
  def _clean():
      _kind.clean_namespace()
      yield
      _kind.clean_namespace()
  ```

- [ ] **Step 1: Write the tests first** (they cannot be run locally — they skip — so "red" here is a careful read against K-5's merged code and C1–C4; run `uv run pytest tests/test_integration_kind_executor.py -q` and confirm it reports **skipped**, and `KIND_INTEGRATION=1 uv run pytest tests/test_integration_kind_executor.py -q` and confirm collection **errors** naming the missing variables):
  - `test_the_tests_run_as_the_pipeline_service_account` — `api().create("/apis/authentication.k8s.io/v1/selfsubjectreviews", {"apiVersion": "authentication.k8s.io/v1", "kind": "SelfSubjectReview"})["status"]["userInfo"]["username"] == PIPELINE_USER`.
  - `test_the_pipeline_identity_has_exactly_the_roles_rules` — `POST /apis/authorization.k8s.io/v1/selfsubjectrulesreviews` with `spec.namespace = NAMESPACE`; `status.incomplete is False`; flatten `status.resourceRules` into `(apiGroup, resource, verb)` triples, dropping the `authorization.k8s.io` and `authentication.k8s.io` self-review rules every user has; the set equals the triples of `infra/kubernetes/pipeline-role.yaml` exactly (no `*`). The same review for namespace `default` yields no triples after the same filter (nothing outside the runs namespace — spec §6.3).
  - `test_the_cluster_runs_the_pinned_versions` — `kubectl version -o json` server `gitVersion` equals the version in `KIND_NODE_IMAGE`; the `kueue-controller-manager` container image ends with `:{KUEUE_VERSION}`; the live `kueue-manager-config` data equals `infra/kueue/config/kind.yaml` (so the restart in `kind-up.sh` took the right file).
  - `test_kueue_serves_the_executors_api_version` — `api().get("/apis/kueue.x-k8s.io")` (discovery, pipeline identity): `preferredVersion.version == KUEUE_API_VERSION` and the served versions are exactly `[KUEUE_API_VERSION]` (C9, K6-R1).
  - `test_tls_rejects_an_untrusted_ca` — `executor(PROCESS_K8S_CA_FILE="/etc/ssl/certs/ca-certificates.crt").probe()` raises `ExecutorUnavailable` (the system bundle does not contain kind's CA); `pytest.fail` (not skip) if that file is missing. Then `executor().probe()` succeeds (K5-R1's TLS on a real server).
  - `test_a_rotated_token_is_used_and_a_bad_one_is_unavailable` — copy the token file to `tmp_path`; an executor on the copy probes; overwrite the copy with a fresh `kubectl -n stac-higher create token stac-higher-pipeline --duration=10m` → the **same** executor probes (token re-read per request, C3); overwrite with `"not-a-token"` → `probe()` raises `ExecutorUnavailable` (401).
  - `test_a_broken_api_url_raises_rather_than_listing_nothing` — `executor(PROCESS_K8S_API_URL="https://127.0.0.1:1").list_launched()` raises `ExecutorUnavailable` (spec §8's list; ADR 0013's "an outage is not no containers").
- [ ] **Step 2: Write `_kind.py`.** `require()` raises `RuntimeError(f"KIND_INTEGRATION=1 but {missing} unset — run scripts/kind-up.sh and source its kind.env")`. `kubectl()` never echoes a token or Secret data; on `check=True` failure it raises with stdout/stderr included. `clean_namespace()` tolerates an empty namespace.
- [ ] **Step 3: Gates** — `uv run pytest -q` (the module skips), `uv run ruff check .`. Commit.
  ```bash
  git commit -m "K-6: kind test harness (pipeline identity for the executor, admin only via kubectl); identity, exact-RBAC, TLS, token-rotation and version checks"
  ```

### Task 6: The executor contract on a real API server

**Files:**
- Modify: `services/pipeline/tests/test_integration_kind_executor.py` (append)

Every test builds its executor with `_kind.executor()`, submits `_kind.code_spec(...)`, and reaps in a `finally`. `ex` below is that executor; `CODE_B64` and `GREETING` are module constants.

- [ ] **Step 1: Write the tests.**
  - `test_a_run_succeeds_end_to_end_and_is_reaped` — code `import os, time; print("K6-MARKER", os.environ["K6_GREETING"], flush=True); time.sleep(2)`, env `{"K6_GREETING": GREETING}` where `GREETING = "hello-from-the-secret"`.
    - After `submit`: the Job (admin `kget("jobs", handle.id)`) carries `kueue.x-k8s.io/queue-name: process-runs` and `kueue.x-k8s.io/priority-class: triggered`; `json.dumps(job)` contains neither `GREETING` nor the code's base64 (C17); the env Secret named `handle.id` has `immutable: true` and an ownerReference whose `uid` is the Job's (read **metadata only**: `kubectl get secret <name> -o jsonpath={.metadata.ownerReferences[0].uid},{.immutable}`); `workload_of(handle)` exists (Kueue's webhook took it — C5, C9).
    - `wait_state(ex, handle, {"exited"})` → `exit == ExitStatus(0)`; the distinct states seen are a subsequence of `pending_capacity → starting → running → exited` (any may be skipped when fast; none may appear out of order; never `lost`).
    - `ex.logs(handle, 65536)` contains `f"{MARKER} {GREETING}".encode()` — the env reached the pod through the Secret, and the platform image ran under PSA `restricted` as uid 10001 on a read-only root (C16).
    - `ex.list_launched()` has one `LaunchedRun` with `handle.id == handle.id`, `run_id == spec.run_id`, `kind == "process"`, `created_at` within 5 minutes of now (UTC).
    - `ex.reap(handle)`; within 60 s admin sees no Job, no Secret `handle.id`, no pod; `ex.reap(handle)` again does not raise; `ex.status(handle).phase == "lost"`.
  - `test_a_failing_run_exits_with_its_code` — code `raise SystemExit(3)` → `exit.exit_code == 3`, `timed_out is False`.
  - `test_an_oom_kill_names_the_memory_limit` — `memory_mb=128`, code `b = b"x" * (512 * 1024 * 1024); print(len(b))` (bytes multiplication touches every page; a bare `bytearray(n)` may not) → `exit.error == oom_error(128)` (C10 row 5), `exit.exit_code == 137`.
  - `test_a_run_past_its_timeout_is_killed_and_keeps_its_log` — `timeout_seconds=5`, code prints `MARKER` then `time.sleep(120)` → `wait_state(…, {"exited"}, timeout=90)`: `exit.timed_out is True`; **before** reap, `pod_of(handle)` exists with `status.reason == "DeadlineExceeded"`, and `ex.logs(handle, 65536)` contains `MARKER` — failure message: `_kind.diagnose_log_loss(handle)` (K6-R12, K5-R4).
  - `test_cancel_of_a_running_run_keeps_its_log` — `timeout_seconds=600`, code prints `MARKER` then sleeps 600; `wait_state(…, {"running"})`; `wait_log(…, MARKER)`; `ex.cancel(handle)`; admin: the pod's `spec.activeDeadlineSeconds == 1` and the Job still exists (C8); `wait_state(…, {"exited"}, timeout=60)` (deadline 1 s + 10 s grace + kubelet sync); `ex.logs(handle, 65536)` still contains `MARKER` — failure message: `diagnose_log_loss` (K6-R12, K5-R5); `ex.cancel(handle)` again returns quietly (idempotent, no second patch needed).
  - `test_cancel_of_a_queued_run_deletes_it` — run A (sleep 60) holds the only CPU; run B → `wait_state(B, {"pending_capacity"})`; `ex.cancel(B)` → within 60 s admin sees no Job B and no Workload for it; `ex.status(B).phase == "lost"`; `ex.status(A).phase == "running"` (cancel touched only B).
  - `test_a_job_deleted_behind_the_executors_back_is_lost` — a running run; admin `kubectl delete job <handle.id> -n stac-higher-runs --cascade=foreground --wait=true --timeout=60s`; `ex.status(handle)` → `RunState("lost", detail=JOB_GONE_DETAIL)` (C10 row 1; while the delete is in flight row 8's `JOB_DELETING_DETAIL` is also `lost` — the test accepts it only before the admin wait returns); `handle.id` is no longer in `ex.list_launched()`.
  - `test_watch_resyncs_then_delivers_events` (async) — consume `ex.watch()` in a task into a list; the first item is `RESYNC` (≤ 15 s); submit a short run; within 60 s at least two events `RunEvent(spec.run_id, handle.id, "process")` arrive (the Job's ADDED/MODIFIED and the Workload's); exactly one `RESYNC` in total; cancel the task (the generator's `finally` closes both streams).
  - `test_watch_resumes_after_the_server_closes_it` (async) — `monkeypatch.setattr(kubernetes_executor, "WATCH_TIMEOUT_SECONDS", 3)` (Ask A3); consume as above; after `RESYNC`, `await asyncio.sleep(8)` (the server ends the stream at least twice); submit a run; an event for it arrives within 60 s, and still exactly one `RESYNC` — the stream resumed from its resourceVersion instead of raising (K6-R13, K5-R21). If K-5 bound the constant at definition time (A3 declined), the test sets it via whatever seam K-5 exposes and the PR says which.
  - `test_pod_security_restricted_is_enforced_and_admits_the_run` — the runs namespace carries `pod-security.kubernetes.io/enforce: restricted`; `kubectl -n stac-higher-runs run psa-probe --image=$KIND_RUNTIME_IMAGE --privileged --dry-run=server` fails with `violates PodSecurity` (so the successful runs above prove K-5's pod complies, rather than proving nothing).
- [ ] **Step 2: Static checks only** — the module skips locally; `uv run ruff check .`; re-read each assertion against the merged K-5 constant it names (Task 0's notes).
- [ ] **Step 3: Commit.**
  ```bash
  git commit -m "K-6: kind tests — submit to success with the env via the Secret, exit codes, OOM wording, timeout and cancel keep the log, queued cancel, lost on delete, watch resync and resume, PSA restricted"
  ```

### Task 7: Kueue behaviour, the admission policy and least privilege

**Files:**
- Create: `services/pipeline/tests/test_integration_kind_kueue.py`

- [ ] **Step 1: Write the tests** (same header as Task 5's module).
  - `test_a_second_run_waits_for_capacity_then_runs` — A: `cpu=1`, prints `MARKER`, sleeps 15; wait A `running`. B: `cpu=1`, quick → `wait_state(B, {"pending_capacity"}, timeout=30)`; `detail` is non-empty and mentions `cpu` (case-insensitive) — Kueue's own message; print B's Workload `QuotaReserved` reason and message (admin) so the PR records v0.20's wording (Ask A1). B never reaches `running` while A is `running`; after A exits, B → `exited` with `ExitStatus(0)` within 90 s.
  - `test_interactive_preempts_triggered_without_spending_an_attempt` (async; K6-R18) — `repo = FakeProcessRepo(runtime={"kind": "inline_python", "retry": {"max_attempts": 3}})`; `run_id = await repo.enqueue_run(process_id="k6-process", revision_id="rev-1", source_id="k6-source", input_items=[{"item_id": "a"}], deferred_until=None, is_test=False, association_id=None)`; `claimed = await repo.claim_run(run_id, now, backend="kubernetes")`; submit A (`run_id=run_id`, priority `triggered`, prints `MARKER`, sleeps 20); `await repo.record_submission(run_id, claimed_at=claimed.claimed_at, handle=a.id, backend="kubernetes", phase="pending_capacity", submitted_at=now, credentials_expire_at=None)`; a `Reconciler` over `repo`, `ex`, `_kind.settings()`, a put-recording storage stub and no-op enqueue callbacks (the `Harness` shape in `tests/test_process_reconcile.py`). Wait A `running`. Submit B (priority `interactive`, sleeps 10). Then:
    - `wait_state(A, {"pending_capacity"}, timeout=60)` — A was preempted; its detail is printed (K5-R6 expects the `PREEMPTED_PREFIX`; whether Kueue v0.20 still shows `Evicted` by then is open — the test asserts the phase, records the detail, and asserts the Workload's conditions (admin) include `Evicted` with reason `Preempted` or a `Preempted=True` condition at the first observation).
    - `await reconciler.reconcile(await repo.get_submitted_run(run_id, backend="kubernetes"))` returns `"phase"` or `"unchanged"`; `repo._row(run_id)` has `status == "running"`, `attempts == 1`, `phase == "pending_capacity"`, the same handle.
    - B reaches `exited` 0 first; then A (same handle — preemption kept the Job) reaches `exited` 0 within 120 s; reconcile again → `"succeeded"`, `attempts == 1`, one log object stored; A's Job is reaped (admin, 60 s).
  - `test_an_unschedulable_run_is_evicted_then_deactivated` — profile = `dataclasses.replace(standard_profile(), backend={"kubernetes": {"queue": "process-runs", "node_selector": {"stac-higher.io/flavor": "nowhere"}, "tolerations": [], "runtime_class": None, "extended_resources": {}}})` (the flavor has no labels, so Kueue admits; no node matches, so the pod never schedules). Poll `status()` up to 240 s collecting distinct `(phase, detail)`: it shows `pending_capacity` with `WAITING_FOR_NODE_PREFIX` (K5-R9), and ends `exited` with `exit.error` starting `DEACTIVATED_PREFIX` (K5-R8; kind config: 30 s timeout, one requeue). Admin: the Workload's `spec.active is False`. The sequence is printed (whether the `evicted (PodsReadyTimeout)` detail of row 11 is visible between cycles is recorded, not asserted).
  - `test_an_evicted_pod_is_lost_not_failed` — a running run; admin eviction: `kubectl create --raw /api/v1/namespaces/stac-higher-runs/pods/<pod>/eviction -f -` with `{"apiVersion": "policy/v1", "kind": "Eviction", "metadata": {"name": <pod>, "namespace": "stac-higher-runs"}}` on stdin; `wait_state(…, {"lost", "exited"}, timeout=60)` must be `lost` with `detail` starting `DISRUPTION_EXIT` (K5-R6). On failure the message says: if the state is `exited` naming `BackoffLimitExceeded`, the pod was gone before `status()` looked — Ask A6's `podFailurePolicy` mapping is the fix.
  - `test_a_job_without_a_queue_or_priority_is_rejected` — as the **pipeline identity**, `api().create(f"/apis/batch/v1/namespaces/{NAMESPACE}/jobs", body)` with a minimal valid Job (`restartPolicy: Never`, the run image, PSA-compliant security context): no labels → `KubeApiError` with `status == 422`, `reason == "Invalid"`, message containing `stac-higher-runs-require-kueue` and `queue-name`; queue label only → 422 naming `priority-class`; queue + `priority-class: urgent` → 422. The same label-less Job via admin `kubectl create --dry-run=server -n default -f -` succeeds (the binding is scoped to the runs namespace, and Kueue ignores queue-less Jobs — `manageJobsWithoutQueueName: false`).
  - `test_a_run_with_an_unknown_priority_dies_rather_than_requeues` — `ex.submit(code_spec(..., priority="urgent"))` raises `RunRejected` (so `HardwareUnavailable` → K-4's claim path makes it `dead`, C14, K6-R9) and admin finds no Job labelled with that run id.
  - `test_the_pipeline_cannot_do_what_it_must_not` — two real calls as the pipeline: `GET …/namespaces/stac-higher-runs/secrets/<a run's Secret>` and `POST …/namespaces/stac-higher-runs/pods` (a bare pod would bypass Kueue) each raise `KubeApiError` with `status == 403` (K5-R20's requeue branch input). Then `SelfSubjectAccessReview`s (`POST /apis/authorization.k8s.io/v1/selfsubjectaccessreviews`, pipeline identity), each `status.allowed is False`: `secrets` get/list/watch, `pods/exec` create, `pods/eviction` create, `serviceaccounts/token` create (minting the run SA's token), `jobs` patch/update, `jobs` create in namespace `default`, `nodes` list, `clusterqueues` and `localqueues` create/update, `workloads` delete/patch, `validatingadmissionpolicies` delete, `roles`/`rolebindings` create in the runs namespace, `configmaps` get in `kueue-system`.
- [ ] **Step 2: Static checks only** (module skips locally; ruff).
- [ ] **Step 3: Commit.**
  ```bash
  git commit -m "K-6: kind tests — quota wait then admission, interactive preempts triggered with no attempt spent (real Reconciler), waitForPodsReady eviction to deactivation, eviction is lost, the admission policy, least privilege"
  ```

### Task 8: The workflow

**Files:**
- Create: `.github/workflows/kind.yml`

**Interfaces:**
- Produces (K6-R2, R5, R14, R15, R16, R17; the header comment names K-6, issue #14, spec §8, and "not a required check: path-filtered"):
  ```yaml
  name: Kind

  on:
    push:
      branches: [main]
      paths:
        - infra/kueue/**
        - infra/kind/**
        - infra/kubernetes/**
        - infra/hardware-profiles/kind.json
        - services/pipeline/src/pipeline/config.py
        - services/pipeline/src/pipeline/process/executor.py
        - services/pipeline/src/pipeline/process/factory.py
        - services/pipeline/src/pipeline/process/hardware.py
        - services/pipeline/src/pipeline/process/http_stream.py
        - services/pipeline/src/pipeline/process/kube_api.py
        - services/pipeline/src/pipeline/process/kubernetes_*.py
        - services/pipeline/src/pipeline/process/reconcile.py
        - services/pipeline/src/pipeline/process/watcher.py
        - services/pipeline/tests/_kind.py
        - services/pipeline/tests/_process_fake.py
        - services/pipeline/tests/test_integration_kind_*.py
        - services/pipeline/uv.lock
        - services/process-runtime/Dockerfile
        - services/process-runtime/entrypoint.py
        - services/process-runtime/docker-bake.hcl
        - scripts/kind-up.sh
        - scripts/kind-diagnostics.sh
        - scripts/check-kueue.sh
        - .github/workflows/kind.yml
    pull_request:
      paths:
        # the same 24 entries as `push`, repeated — helm.yml's convention
        - infra/kueue/**
        # …
    schedule:
      - cron: "17 6 * * 1"
    workflow_dispatch:

  permissions:
    contents: read

  concurrency:
    group: kind-${{ github.ref }}
    cancel-in-progress: ${{ github.event_name == 'pull_request' }}

  jobs:
    static:
      name: Kueue manifests · kubeconform
      runs-on: ubuntu-latest
      timeout-minutes: 10
      steps:
        - uses: actions/checkout@v7
        - uses: astral-sh/setup-uv@v10.0.1
        - name: Install kubeconform
          run: |
            curl -sSfL -o kubeconform.tar.gz \
              https://github.com/yannh/kubeconform/releases/download/v0.8.0/kubeconform-linux-amd64.tar.gz
            echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  kubeconform.tar.gz" | sha256sum -c -
            tar -xzf kubeconform.tar.gz kubeconform
            sudo install kubeconform /usr/local/bin/kubeconform
        - name: Validate against Kueue's own CRDs
          run: scripts/check-kueue.sh "$RUNNER_TEMP/check-kueue"

    kind:
      name: Kubernetes executor · kind + Kueue
      needs: [static]
      # Pinned like the e2e job (K6-R2): kind v0.33.0 on the ubuntu-latest
      # image (26.04) is unverified, and the runner must not change the
      # cluster under the gate. Unpin when a weekly run on it is green.
      runs-on: ubuntu-24.04
      timeout-minutes: 30
      steps:
        - uses: actions/checkout@v7
        - uses: astral-sh/setup-uv@v10.0.1
          with:
            enable-cache: true
            cache-dependency-glob: services/pipeline/uv.lock
        - name: Install the pipeline
          working-directory: services/pipeline
          run: uv sync --frozen --extra dev
        - uses: docker/setup-buildx-action@v4
        # K6-R5: the real platform runtime, from its own context (the
        # repo-root .dockerignore is not involved), reading the layer cache
        # containers.yml writes and never writing it.
        - name: Build the run image
          uses: docker/bake-action@v6
          with:
            source: .
            files: ./services/process-runtime/docker-bake.hcl
            targets: runtime
            load: true
            set: |
              runtime.tags=stac-higher-process-runtime:kind
              runtime.platform=linux/amd64
              runtime.cache-from=type=gha,scope=process-runtime
        - name: Install kind and kubectl (pinned, checksummed)
          run: |
            set -euo pipefail
            source infra/kind/versions.env
            curl -sSfL -o kind "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64"
            echo "${KIND_SHA256}  kind" | sha256sum -c -
            curl -sSfL -o kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
            echo "${KUBECTL_SHA256}  kubectl" | sha256sum -c -
            sudo install kind kubectl /usr/local/bin/
        - name: Create the cluster, install Kueue, apply RBAC and queues
          run: scripts/kind-up.sh "$RUNNER_TEMP/kind"
        - name: Load the run image onto the node
          run: kind load docker-image stac-higher-process-runtime:kind --name stac-higher
        - name: Integration tests (as the pipeline ServiceAccount)
          timeout-minutes: 15
          working-directory: services/pipeline
          env:
            # K6-R16: raise with every test added to the two kind modules.
            KIND_MIN_TESTS: "24"
          run: |
            set -euo pipefail
            set -a; source "$RUNNER_TEMP/kind/kind.env"; set +a
            uv run pytest tests/test_integration_kind_executor.py tests/test_integration_kind_kueue.py \
              -v -rA --junitxml="$RUNNER_TEMP/kind/junit.xml"
            python3 -I - "$RUNNER_TEMP/kind/junit.xml" "$KIND_MIN_TESTS" <<'PY'
            import sys, xml.etree.ElementTree as ET
            root = ET.parse(sys.argv[1]).getroot()
            suite = root if root.tag == "testsuite" else root.find("testsuite")
            tests, skipped = int(suite.get("tests")), int(suite.get("skipped"))
            if skipped or tests < int(sys.argv[2]):
                sys.exit(f"kind suite: {tests} tests, {skipped} skipped; expected >= {sys.argv[2]} and none skipped")
            PY
        - name: Diagnostics
          if: failure()
          run: scripts/kind-diagnostics.sh "$RUNNER_TEMP/kind-diagnostics" "$RUNNER_TEMP/kind/admin.kubeconfig"
        - name: Upload diagnostics
          if: failure()
          uses: actions/upload-artifact@v7
          with:
            name: kind-diagnostics
            path: ${{ runner.temp }}/kind-diagnostics
            if-no-files-found: ignore
            retention-days: 7
  ```
  The `pull_request` list is the `push` list verbatim (no YAML anchors — no workflow in the repo uses them and the repo's actionlint is pinned at 1.7.12); a static test is not worth it, but the PR reviewer diffs the two lists.

- [ ] **Step 1: Write the workflow.**
- [ ] **Step 2: Lint locally where possible:** `actionlint` is run by `ci.yml` in Docker (lead-only locally); the implementer checks YAML syntax with `uv run --no-project --with pyyaml python -I -c "import sys, yaml; yaml.safe_load(open(sys.argv[1]))" .github/workflows/kind.yml` and reads every `run:` block under `bash -n` (copy each into a scratch file).
- [ ] **Step 3: Commit.**
  ```bash
  git commit -m "K-6: kind CI workflow — kubeconform gate, then a kind cluster with Kueue, the real runtime image kind-loaded, the integration suite as the pipeline SA (no silent skips), diagnostics on failure"
  ```

### Task 9: Docs

**Files:** `infra/kueue/README.md` (new), `docs/FEATURES.md`, `docs/ISSUES.md`, `services/pipeline/README.md`, `infra/kubernetes/README.md` (one line — K-5's file; only the "K-6 applies this to kind" sentence, if K-5 wrote it differently).

- [ ] **`infra/kueue/README.md`** (short): what each file is (`kueue.env`, `config/production.yaml` vs `config/kind.yaml`, `common/`, `kind/`); the install order on any cluster — Kueue `manifests.yaml` for `KUEUE_VERSION` (server-side apply, checksum) → `kueue-manager-config` from `config/production.yaml` + controller restart → `infra/kubernetes/` (K-5) → `common/` → the deployment's flavors and ClusterQueue (quota = the node pool's ceiling, spec §7.3; GPU flavors with `nodeLabels` equal to the profile's `node_selector` and the GPU toleration, spec §3.4); how R-8 reuses `common/` and the kind shapes (K6-R8: templates + drift tests, never a fork); that the admission policy answers 422 so a mislabelled run dies rather than parks (K6-R9); how to bump Kueue (both lines of `kueue.env`, read the release's "Actions Required", run the kind leg); and the Kueue pod-webhook note below.
- [ ] **FEATURES.md:** a `K-6 · Kueue manifests + kind CI leg` row after K-5: Kueue v0.20.8 pinned (`kueue.env`), the production controller config (waitForPodsReady 10 m / 3 requeues, `batch/job` only, no queue-less Jobs), priority classes, LocalQueue, the queue/priority admission policy, the 1-CPU kind flavor and ClusterQueue, `kind.json`, `kind.yml` (kubeconform against Kueue's CRDs; kind 1.34.11 with the real runtime image; the executor suite as the pipeline ServiceAccount with exact-RBAC, PSA, quota, preemption, eviction, cancel/timeout log checks).
- [ ] **ISSUES.md** (next free numbers from Task 0):
  - **New · What the kind leg cannot show** 🟡 — GPU flavors, extended resources, tolerations and the device plugin (no GPU on runners; covered by K-5's mapping unit tests and K-9's cluster gate); Karpenter NodePools and the quota-equals-limits rule (I-92; K-8/R-11); the registry mirror rewrite and the pull-through cache (R-8 #144, deployment spec §5); NetworkPolicy egress for runs and the pipeline (R-8; kind's default CNI is not the EKS one); the in-cluster pipeline pod path — projected token/CA and the service-host URL (K6-R4; R-11); image-scan Jobs (the scanner image is not built in the leg; K5-R11); a real `410 Gone` (K6-R13); production `waitForPodsReady` timing (K6-R7; K-9); Kubernetes newer than 1.34 (K6-R2).
  - **New · Kueue's pod webhook couples every pod creation to Kueue** 🟡 — `mpod.kb.io` (failurePolicy `Fail`, every namespace except `kube-system`/`kueue-system`) stays registered even with the pod integration disabled (it answers no-op), so app and pipeline pods cannot be created while Kueue's webhook is down. The cluster installer should narrow that webhook's `namespaceSelector` to the runs namespace, or accept and monitor it. Tracked in: GitHub #16 (K-8).
  - **New · The kind workflow is path-filtered and not a required check** 🟢 — a change outside its paths that breaks the leg is caught by the weekly run (K6-R14).
  - **I-92** — add: Kueue's `waitForPodsReady` is configured (10 m, three requeues, then deactivation → the run dies; K6-R7), the safety net this entry asked for.
  - If Task 10's CI loop leaves a K6-R12 or A6 fallback undecided, an entry naming it and its `fix/` PR.
- [ ] **`services/pipeline/README.md`:** a "Kubernetes integration suite (kind, K-6)" section — the suite skips unless `KIND_INTEGRATION=1`; how a machine with Docker and kind runs it: `scripts/kind-up.sh "$PWD/.kind"`, `docker buildx bake -f services/process-runtime/docker-bake.hcl runtime --set runtime.tags=stac-higher-process-runtime:kind --load`, `kind load docker-image stac-higher-process-runtime:kind --name stac-higher`, `set -a; . .kind/kind.env; set +a`, `uv run pytest tests/test_integration_kind_*.py -v`, teardown `kind delete cluster --name stac-higher`; that the executor runs as `stac-higher-pipeline` and the admin kubeconfig is harness-only.
- [ ] Gates (`npm run verify`; pytest; ruff) and commit: `git commit -m "K-6: docs — infra/kueue README, FEATURES row, kind blind spots and the Kueue pod-webhook coupling in ISSUES, I-92 safety net, how to run the kind suite"`.

### Task 10: PR, the CI loop, hand-offs (lead only)

- [ ] Worktree: `npm run verify`; `uv run pytest -q`; `uv run ruff check .`; `scripts/check-kueue.sh`. Rebase onto `main`; gates again. Push; `gh pr create --base main` titled `K-6: Kueue manifests and a kind CI leg for the Kubernetes executor`, body starting `Closes #14`: the gates; "no migration"; deviations from spec/issue (Kueue v0.20.8 not v0.19.2 — K6-R1; the policy also requires a known priority — K6-R9; Kueue manages `batch/job` only — K6-R7; kind uses shortened waitForPodsReady timing — K6-R7); every C1–C18 difference found in Task 0; Asks A1–A6 that K-5 did or did not take.
- [ ] **The iterate-on-CI loop.** The first run of `kind.yml` is the first time this code meets a cluster. Watch it (`gh run watch`); on failure `gh run download <run-id> -n kind-diagnostics` and read the Kueue controller log, events and pod YAML. Classify each failure:
  1. **Harness, script or workflow** (a wrong path, a wait too short, a `kubectl` flag) → fix on this branch.
  2. **A K-5 assumption a real server disproves** (TLS/SAN, a field name, a Kueue reason or condition, A6's vanished pod, K6-R12's log) → a `fix/` PR against K-5's code on `main` first (K-6 does not edit executor code), then rebase this branch. For K6-R12 or A6 the named fallback is that fix; raise it on epic #2.
  3. **A Kueue behaviour this plan could not settle** (the open questions in the report) → adjust the assertion to what Kueue does if it is consistent with spec §7 and K-5's table, and say so in the PR; otherwise case 2.
  K-6 merges only green. If a case-2 fix is large, the lead may mark exactly the affected test `pytest.mark.xfail(strict=True, reason="<fix/ PR>")` — strict, so it fails loudly once fixed — and the JUnit check accepts xfail (it is not a skip). Record the iteration count and the root causes in the PR.
- [ ] Paste the evidence the tests print into the PR: Kueue's `QuotaReserved` reason/message for the capacity wait, the preempted run's detail and Workload conditions, the unschedulable run's state sequence, the job runtime.
- [ ] Squash-merge when green.
- [ ] **Hand-off to R-8 (#144)** — comment: reuse `infra/kueue/common/` as chart templates with only the namespace parameterised and a drift test against the files; render the ResourceFlavors and ClusterQueue from values (quota = NodePool limits) with a drift test that kind-equivalent values reproduce `infra/kueue/kind/`; keep the ClusterQueue's namespace selector on the runs namespace; Kueue itself is a cluster prerequisite pinned by `infra/kueue/kueue.env` + `config/production.yaml` (K6-R8).
- [ ] **Hand-off to K-8 (#16)** — comment: `config/production.yaml` is the controller config to install; the pod-webhook coupling entry in ISSUES; GPU flavors belong beside `eks.json` with `nodeLabels` equal to each profile's `node_selector`; quotas equal NodePool limits (I-92).
- [ ] `git worktree remove .claude/worktrees/k6-kueue-kind`.

## Self-review

- **Issue coverage.** ResourceFlavor per profile family → Task 2 (`cpu-default`; GPU families arrive with K-8's profile set, pinned by Task 3's flavor/selector test). ClusterQueue `process-runs` BestEffortFIFO + `withinClusterQueue: LowerPriority` → Task 2, proven by Task 7's preemption test. LocalQueue → Task 2. WorkloadPriorityClasses `interactive` > `triggered` → Task 2. `waitForPodsReady` → Task 1, proven by Task 7's eviction-to-deactivation test. Admission policy rejecting label-less Jobs → Task 2, proven by Task 7. `kind.json` with a 1-CPU quota → Tasks 2–3, `pending_capacity` proven by Task 7. CI job: kind, Kueue by server-side apply, integration tests → Tasks 4, 8, 5–7. `.dockerignore` gotcha → Global Constraints (no repo-root-context image). Spec §8's list: submit → pending under 1 CPU → admitted → exited (Task 7), cancel (Task 6), reap (Task 6), `list_launched` under a broken URL raises (Task 5).
- **The brief's test list.** Submit → running → succeeded with log: Task 6. `pending_capacity` then admission: Task 7. Preemption without an attempt: Task 7 (real Reconciler). Cancel AND timeout keeping the log, fallback named and decided by diagnosis: Task 6 + K6-R12. OOM wording: Task 6. `lost` on a deleted Job: Task 6 (and eviction, Task 7). `watch()` events: Task 6. Label-less Job rejected: Task 7. RBAC denial: Tasks 5 and 7.
- **Type and name consistency.** `_kind.executor()` → `build_executor` (C2); `_kind.api()` → `KubeApi.from_settings` (C3) for reviews and raw denials only; constants asserted come from `kubernetes_executor` (A5) or `executor` (`oom_error`, `PRIORITIES`, `RunRejected`); `KUEUE_API_VERSION` from `kubernetes_manifests` (C9) is pinned against the manifests (Task 2) and the live server (Task 5); the LocalQueue and namespace names are pinned against K-5's settings defaults (Task 2); `KIND_MIN_TESTS = 24` = 17 tests in `test_integration_kind_executor.py` (Tasks 5–6) + 7 in `test_integration_kind_kueue.py` (Task 7).
- **Proportion.** Manifests and the workflow are given in full because their exact values are the deliverable; test bodies are described by their assertions.

---

## Addendum A · Task 0 reconciliation with K-5 as merged (2026-11-26)

K-5 merged as `d5883f7` (PR #205). This addendum folds what changed into the
plan. Where it and the body above disagree, the addendum wins.

### Versions re-checked

`gh release list` (2026-11-26): Kueue v0.20.8 is still the newest 0.20 patch
(v0.19.15 and v0.21.0-rc.2 aside); kind v0.33.0 is still the newest release,
and its notes list `kindest/node:v1.34.11@sha256:44e222ee…d67d`. Re-downloaded
and re-hashed: Kueue `manifests.yaml` (`ecbc26a9…290a`), `kind-linux-amd64`
(`aee61515…8e0d`, the release's `.sha256sum`), kubectl v1.34.11 linux/amd64
(`8efbb943…b7a8`, dl.k8s.io's `.sha256`), kubeconform v0.8.0 darwin-arm64
(`f84f4dfb…9d67`) and its `openapi2jsonschema.py` (`d145babf…81f3`). All pins
in Global Constraints stand.

### Contract points C1–C18

All hold, with these differences:

- **C4.** The Role has a sixth rule, `networking.k8s.io networkpolicies get`
  (the executor's isolation precondition). The rules-review test compares
  against the file, so it needs no edit.
- **C5.** The Job now carries `podFailurePolicy: FailJob on DisruptionTarget`
  (Ask A6 taken) beside `backoffLimit: 0`.
- **C10.** Rows renumbered: row 3 now also maps a Job `Failed` with reason
  `PodFailurePolicy` to `lost` (`POD_FAILURE_POLICY_REASON`), and a row 6b
  maps a pod the node refused before any container ran to `lost`
  (`POD_FAILED_PREFIX`). Row 12 reads `QuotaReserved` by status only (A1).
- **C13.** `probe()` also runs the isolation check; `submit()` runs it before
  the Job create.
- **C14.** Wider: a 422 **or** a 403 whose Status message names an admission
  webhook's denial (`KubeApiError.admission_denied`) raises `RunRejected`;
  any other 403 is `ExecutorUnavailable`.
- **New precondition (addendum A18 of K-5).** The executor refuses to start
  any run (`ExecutorUnavailable`, requeue) unless the runs namespace holds
  NetworkPolicy `stac-higher-runs-default-deny` with `podSelector: {}`,
  `policyTypes` ⊇ {Ingress, Egress} and no rules. A passed check is cached
  60 s per (API URL, namespace) in module state
  (`kubernetes_executor.reset_isolation_cache()`).
- **Network level.** A process run must ask for network level `isolated` and
  no hosts, else `RunRejected`. `code_spec` uses the RunSpec defaults, which
  satisfy that.

Asks A1–A6: **all taken** (A5's constants exist: `JOB_GONE_DETAIL`,
`JOB_DELETING_DETAIL`, `DISRUPTION_EXIT`, `PREEMPTED_PREFIX`,
`WAITING_FOR_NODE_PREFIX`, `DEACTIVATED_PREFIX`, plus `POD_FAILED_PREFIX`,
`START_FAILURE_PREFIX`, `CANCEL_POD_DEADLINE_SECONDS`).

### Rulings added or changed

- **K6-R19 · The default-deny NetworkPolicy ships as
  `infra/kubernetes/runs-default-deny.yaml`.** It is a runs-namespace object
  every deployment needs, like the namespace, the run ServiceAccount and the
  Role, and not a Kueue object; R-8 already renders `infra/kubernetes/` with a
  drift test per file, so the fifth file rides the same mechanism. This
  amends Global Constraints ("do not touch `infra/kubernetes/`"): one new file
  and K-5's README lines that said the directory holds no NetworkPolicy. A
  static test proves the file passes K-5's own `isolation_problem()` and is
  named `DEFAULT_DENY_POLICY`. **No allow policy is shipped for kind:** the
  kind runs need no network (code and env arrive through the Secret, logs
  through the kubelet); R-8 owns the `isolated`/`scanner` allow policies.
- **K6-R20 · NetworkPolicy enforcement is proven, then tested.** kind's
  default CNI enforces NetworkPolicy from **v0.24.0** (release notes:
  "Out-of-the-box support for network policy via
  sigs.k8s.io/kube-network-policies"; v0.25.0 adds a DNS fix for network
  policies), so the v0.33.0 pin qualifies. A scratch namespace proves it
  first (a client reaches a server pod and an external IP with no policy, and
  stops reaching the server once a default-deny is applied); only then does a
  real run, submitted through the executor, prove it cannot connect to
  `169.254.169.254` or to an external address.
- **K6-R21 · The admission policy is I-184's, not K6-R9's (supersedes
  K6-R9).** `infra/kueue/common/admission-policy.yaml` holds two policies and
  two bindings, all bound to the runs namespace by `kubernetes.io/metadata.name`,
  `failurePolicy: Fail`, every validation `reason: Invalid` (422 → the
  executor's `RunRejected`):
  - `stac-higher-runs-jobs` on `batch/v1 jobs`: on CREATE and UPDATE the queue
    label and a priority of `interactive`/`triggered`; on CREATE only (Kueue
    and the garbage collector update our Jobs; a shape rule on UPDATE could
    strand a foreground delete) the pod template's `serviceAccountName ==
    stac-higher-run`, `automountServiceAccountToken == false`, `emptyDir`
    volumes only, image pull Secrets named `<job>-pull` only, no init or
    ephemeral containers, no container `env`, `envFrom` only a `secretRef`
    naming the Job itself.
  - `stac-higher-runs-pod-updates` on `v1 pods` UPDATE, matched only when the
    requester is `system:serviceaccount:stac-higher:stac-higher-pipeline`:
    `activeDeadlineSeconds` present and not raised; labels, annotations,
    ownerReferences and finalizers unchanged; and every pod-spec field the API
    server lets an UPDATE change (containers, initContainers, tolerations,
    schedulingGates, terminationGracePeriodSeconds, nodeSelector, affinity)
    unchanged.
  Every validation message starts with a rule id (`[queue-label]` …).
  **Drift:** one table, `services/pipeline/tests/_admission_rules.py`, maps
  each rule id to the problem string K-5's pure rules in
  `tests/test_kubernetes_rbac.py` report, plus a mutation that violates it. A
  static test requires the YAML's ids, the table's ids and the problem
  strings the pure functions can return (read from their source with `ast`)
  to correspond exactly, and that each mutation makes the pure rules report
  exactly its problem; the kind tests apply the same mutations to a real
  server and expect a 422 naming the same id. The pure rules stay K-5's (not
  moved): K-6 imports them.
- **K6-R22 · Each unverified K-5 assumption is settled by a recording
  scenario.** A scenario drives the cluster once, never raises, and records
  what it saw (states, Job and Workload conditions, pod status). Separate
  tests then assert, each with a failure message naming which documented
  outcome occurred and what follows (`infra/kubernetes/README.md`'s K-6 list).
  Observations are appended to `$KIND_OUT/observations.jsonl`, uploaded on
  every run (no Secret data: only object status and test-made text).
- **K6-R23 · `waitForPodsReady.timeout` in `config/production.yaml` must
  exceed `kubernetes_executor.START_FAILURE_GRACE_SECONDS`** (a static test):
  otherwise Kueue evicts a pod still pulling before the executor would fail
  it. The kind variant's 30 s is the deliberate exception (no kind test pulls
  an image).
- **K6-R24 · Kueue's job-webhook denial is provoked with a queue label that
  is not a DNS subdomain.** v0.20.8's `ValidateJobOnCreate` →
  `ValidateQueueName` → `ValidateLabelAsCRDName` refuses it, and
  controller-runtime turns a field-error list into `Denied()` (HTTP 403,
  "admission webhook … denied the request"). The test drives it through
  `submit()` (the executor's default queue set to the bad name) and records
  the status and message.
- **K6-R25 · `kind-up.sh` waits for the admission policies to enforce** (a
  server dry-run of a label-less Job is refused) and fails on any
  `status.typeChecking.expressionWarnings`, so a CEL type error fails the
  bring-up step, not thirty tests.
- **Budget.** Test step `timeout-minutes: 25`, job 45 (the network, admission
  and scenario tests added ~6 minutes). The JUnit gate accepts xfail and
  nothing else that skips; `KIND_MIN_TESTS` equals the number of test
  functions in the two kind modules (Task 8 counts them).
- **ISSUES numbering** starts at I-185 (I-184 is the last, from K-5).

### Addendum A, continued · lead rulings and R-8's asks

- **K6-R26 · Run labels are closed and tied to the Job name (lead ruling,
  security review).** Two more Job CREATE rules: `[run-labels]` — the pod
  template's (`stac-higher.run-kind`, `stac-higher.network-level`) is
  `(process, isolated)`, or `(image_scan, scanner)` when the Job name starts
  `stac-scan-` (`kubernetes_manifests.NAME_PREFIX`); `[template-labels]` —
  the template repeats the Job's run-kind, network-level and run-id labels.
  The pure twin in `tests/test_kubernetes_rbac.py` gained both (and its
  admitted-Job test now names each Job as the executor does, `job_name`);
  every Job the executor builds passes. A pod's labels cannot change after
  creation (`[pod-update-metadata]`; the kind test relabels a run into
  `scanner` and expects a 422). Residual in I-184: a scan-shaped Job still
  gets the scanner's egress.
- **R-8's asks (taken).** (1) The pipeline identity appears once in
  `admission-policy.yaml` (the pod-update match condition) and the run
  ServiceAccount once; the deadline rule is "present and not above the old
  value" (set or lowered, never raised or removed — the API server itself
  also refuses a raise or removal). (2) `scripts/check-kueue.sh
  --schemas-only <dir>` writes the pinned v1beta2 schemas to `<dir>/schemas`
  without needing kubeconform. (3) `common/` holds one object per document,
  stable names, nothing deployment-specific but the runs namespace and the
  two identities above; kind capacity is under `kind/`. (4) `kind.json`'s
  `backend.kubernetes` block is exactly the plan's shape. Also added:
  `test_a_flavors_tolerations_reach_the_admitted_pods` (a second flavor with
  `tolerations`, the node tainted for the test only) for R-8's taint plan.
- **Counts.** `KIND_MIN_TESTS = 51` (23 in `test_integration_kind_executor.py`,
  28 in `test_integration_kind_kueue.py`, parametrized cases counted). The
  JUnit gate is `.github/scripts/kind-junit-gate.py` (xfail allowed, any other
  skip fails; a failure table goes to the job summary).
- **K6-R27 · Run isolation is asserted once enforcement has settled; the
  startup window is measured.** The first CI run showed a run pod under the
  runs namespace's default-deny reaching IMDS, the API service and the
  internet. The run's connects started about 0.5 s after the kubelet admitted
  the pod. kindnet's kube-network-policies (the commit kind v0.33.0 pins)
  diverts to its NFQUEUE only the IPs of pods it already knows that a policy
  selects (`ManagedIPs`, an nftables set synced at most once a second).
  Packets from an IP not yet in that set are never evaluated, and a packet
  whose source pod is unknown matches no policy. The probe now records three
  phases: `t0`, a polled `window` timing when egress is first blocked, and
  `settled` (from 15 s). Only `settled` is asserted. The window is recorded
  as an observation and a `::warning::`, for the lead to rule on (ISSUES,
  R-8/R-11 strict enforcing mode, a possible executor mitigation).
- **K6-R28 · Review fixes (R1–R8).** Each rule added in review has a rule id,
  a mutation, a pure twin and a kind case. The Job policy now refuses
  placement and host fields the executor never sets, requests that differ
  from limits, and missing deadlines. Label rules admit an UPDATE that leaves
  labels unchanged. A third policy limits Secret types. Kueue's webhooks are
  narrowed to the runs namespace at install time
  (`scripts/kueue-narrow-webhooks.py`, `infra/kueue/webhooks.txt`, checked by
  `check-kueue.sh`); the kind leg proves that a Deployment elsewhere creates
  with Kueue down. `KIND_MIN_TESTS = 61`, and xfails are allowed only when
  named in `KIND_ALLOWED_XFAIL`.
- **K6-R29 · The startup race is a property of the CNI class (lead ruling).**
  The second CI run confirmed it: all three targets were reachable at t0, the
  window ran from 0.26 s (last reachable) to 0.51 s (first blocked), and all
  were blocked once settled, with `hostNetwork` false. K-6 does not change
  the executor; #207 owns the mitigation. The isolation test asserts the
  settled state and that `first_blocked_s` ≤ 10 s, and records t0. I-188
  records the residual. `infra/kubernetes/README.md` makes strict
  enforcement a cluster prerequisite for R-8/R-11
  (`NETWORK_POLICY_ENFORCING_MODE=strict` on the VPC CNI; the Auto Mode
  NodeClass's `networkPolicy: DefaultDeny`, to be proven live) and adds the
  live check that a run connecting at t=0 is blocked.
- **K6-R30 · Re-review fixes (N1–N7).** Four new Job CREATE rules, each with
  a rule id, mutations, a pure twin and kind cases:
  - `[suspended]`, then `[no-owner]` (in that order): no owner references,
    and the Job must arrive suspended.
    - The pure twin models Kueue's mutating webhook (`kueue_mutated`), which
      runs before validating admission.
    - Kueue leaves a Job unsuspended when its controller owner is a Job Kueue
      manages; the kind case gives the violating Job a real run Job as its
      controller owner, which shows that path.
    - With no owner, an explicit `suspend: false` cannot reach the policy
      unsuspended, because Kueue rewrites it.
  - `[no-probes]`: no probes or lifecycle hooks.
  - `[deadlines]`: now also bounded — pod 86 400 s, Job 87 000 s, grace 30 s.
    A static test ties these numbers to the code.
  - `[volumes]`: now memory-backed and sized only.
  The Kueue-down test waits for zero ready webhook endpoints. `KIND_MIN_TESTS`
  is 69. R-8 asks for a profile param resource to bound `nodeSelector`,
  `tolerations` and `runtimeClassName`.
