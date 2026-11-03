# Deployment artifacts (Phase 8, part 1) — design

**Date:** 2026-11-02
**Status:** **approved 2026-11-03** by the lead, with Q2 and Q3 settled as recorded in §13; Q1 stays open for the cluster team.
**Tracking:** epic #136.
**Decision record:** [ADR 0024](../../decisions/0024-deployment-artifacts.md)
(accepted) — publish-don't-deploy, Terraform/OpenTofu for AWS, Helm for
Kubernetes, eoapi-cdk not adopted, services on EKS.
**Scope source:** ROADMAP §9 Phase 8; the K spec
([2026-09-02 process compute](2026-09-02-process-compute-k8s-kueue-design.md))
§6, §7 and §11; `docs/backend.md` (what the local stack runs).

## 1. Intent

The team wants the whole platform running in an AWS account: first a shared
**team test environment in commercial us-east-1** that doubles as the first
full demo for an audience that has never seen it. It is not a FISMA High
production environment.

The demo walks through connections, extractors, products, processes and
destinations; the GOES loop end to end, including the GeoColor process run;
and the Icechunk virtual cube (Z queue).

This repo is **public** and must carry **no account details**. So it does
not deploy anything. It **publishes versioned artifacts** — container images,
a Helm chart, Terraform/OpenTofu modules — and a separate **private
deployment repo** (GitLab or GitHub, undecided; this spec must not care)
pulls them, supplies the account's values and deploys. The EKS cluster
itself is created by yet another repo and is assumed to exist.

This spec covers only what this repo builds and publishes, the contract a
deployment repo relies on, and the code changes without which the published
artifacts cannot run in AWS. The deployment repo, the cluster repo and the
Phase 8 load gate are out of scope.

## 2. Goals, non-goals, anti-goals

**Goals**
- A tagged release of this repo produces everything a deployment needs, each
  piece versioned by that one tag.
- A deployment repo can go from an existing EKS cluster to a running platform
  with only: a root Terraform module calling ours, a values file, and its own
  pipeline. No forks, no patches, no copying templates out of this repo.
- Every artifact is validated in this repo's CI without an AWS account.
- Commercial us-east-1 first; **GovCloud-portable where it is cheap** (§9).
- The first environment needs **no GPUs** (GeoColor and the extractors are
  CPU work).

**Non-goals**
- The deployment repo's pipeline, backend config, tfvars and values.
- VPC, EKS cluster, cluster add-ons, DNS zones (the cluster repo / account).
- The Phase 8 load gate, the SQS queue backend, scheduler HA, multi-region.
- GPU NodePools and GPU hardware profiles (#16, re-scoped — §12).
- FISMA High controls beyond what falls out of sensible defaults.

**Anti-goals** (failure even if it works)
- Any account id, ARN, hostname, CIDR, or secret committed to this repo.
- A test environment on the public internet running `AUTH_MODE=dev-bypass`.
- Two IaC engines (CloudFormation via CDK *and* Terraform) for one stack.
- Terraform managing Kubernetes objects whose CRDs it also installs (the
  plan-time CRD problem).
- Artifacts that only work with this team's GitLab, or only with GitHub.
- Long-lived static AWS keys for the platform's own access to AWS (its
  bucket, STS, KMS). Credentials a user seals into a connection are the
  user's, by design (`docs/connections.md`).

## 3. Why not eoapi-cdk (summary; reasoning in ADR 0024)

eoapi-cdk (v11.8.1) runs stac-fastapi, titiler-pgstac, tipg and
stac-auth-proxy as **Lambda + API Gateway**; on EKS those constructs do not
apply. What remains is `PgStacDatabase` (RDS, parameter group, a Secrets
Manager secret, a bootstrapper Lambda installing pgstac, optional PgBouncer
EC2). Taking CDK for that one construct means a second IaC engine with its
own state, and a pgstac installer that competes with our `pypgstac migrate`
(ADR 0001). We take its **parameter-group tuning** as a reference and build
RDS in Terraform.

**eoapi-k8s** (Helm chart 0.17.4) *is* adopted as a chart dependency for the
catalog plane (§6.2): it deploys stac-fastapi, titiler-pgstac (`raster`),
tipg (`vector`) and stac-auth-proxy, supports an external database
(`postgresql.type: external-secret`), ingress and autoscaling.

## 4. Three repos, one contract

| Repo | Owns |
|---|---|
| **cluster repo** (exists or will) | VPC, EKS cluster, cluster add-ons (§4.1) |
| **this repo** (public) | Images, Helm chart, Terraform modules, a placeholder example, CI validation, releases |
| **deployment repo** (private, per org) | Root module + state backend, tfvars, Helm values, its CI pipeline, running the demo seed |

### 4.1 What the cluster must already provide

Written into the chart's README as a prerequisites list:

- EKS **1.34+** (Kueue's tested range), **Auto Mode** (managed Karpenter,
  built-in load balancing, Pod Identity agent).
- **NetworkPolicy enforcement** enabled.
- **Kueue v0.19+ controller** installed (cluster-scoped CRDs and webhooks),
  configured with `waitForPodsReady` **on** (K spec §7.3) and
  `managedJobsWithoutQueueName: false` (§7.1). These live in Kueue's own
  install configuration, which the chart cannot set.
- **External Secrets Operator** with a `ClusterSecretStore` for Secrets
  Manager — *or* the deployment creates the Kubernetes Secrets itself; the
  chart supports both (§6.4).
- An **IngressClass** (Auto Mode's ALB class, or any other) and a way to get
  TLS certificates onto it (an ACM ARN in annotations, or cert-manager).
- Optional: Prometheus Operator (the chart ships `ServiceMonitor`s behind a
  toggle).

The chart **does** ship the app-specific cluster-scoped objects (Kueue
ResourceFlavors, ClusterQueue, WorkloadPriorityClasses, the CPU NodePool,
the ValidatingAdmissionPolicy) behind a single `processRuns.clusterObjects`
toggle, because the K spec §7.3 requires NodePool `limits` to equal the
Kueue quota and both to match the hardware-profile file — they must change
together. A cluster team that will not let an app deploy create
cluster-scoped objects turns the toggle off and owns them, using the
chart's rendered output as the reference (open question Q1, §13).

## 5. Artifact 1 — container images

Published today by `.github/workflows/containers.yml` to
`ghcr.io/techtraverse/stac-higher-<name>`, multi-arch on push, semver tags
on `v*`, a `sha-<long>` tag on every build.

| Image | Status | Needed for |
|---|---|---|
| `app` | published | the app |
| `pipeline` | published | worker, scheduler, `pypgstac migrate`, demo seed |
| `titiler` (derived) | published, **linux/amd64 only** (upstream base) — the raster pods carry an amd64 node selector | raster tiles |
| `image-scanner` | published | scan runs |
| `process-runtime`, `process-runtime-stactools` | published | process and extractor runs |
| `auth-proxy` (derived, `infra/proxy-policy/`) | **not published** — add to the matrix | the ADR 0015 write policy |
| `cube-server` | published (Z-7, #121) | Icechunk tiles + EDR |
| `process-runtime-cuda` | #15 (K-7) | GPU profiles — not the first environment |

Third-party images the chart runs (stac-fastapi-pgstac, tipg) stay pinned
by digest exactly as `docker-compose.yml` pins them today, and the
stac-fastapi/auth-proxy pair is bumped together (ADR 0015).

**Registry path to the cluster.** The cluster never pulls from GHCR
directly. The Terraform `registry` module creates an **ECR pull-through
cache rule** for `ghcr.io` (and Docker Hub, Quay as inputs), so a deployment
references `<account>.dkr.ecr.<region>.amazonaws.<partition-suffix>/ghcr/techtraverse/stac-higher-app@sha256:…`.
This also covers GovCloud, which has no ECR Public. ECR's GHCR upstream
requires a credential secret even for public images; the module takes its
Secrets Manager ARN as an input.

**Image rows keep their upstream reference; only the pull is mirrored.**
The platform runtime images are scanned and approved like any user image
(`docs/backend.md` "Base images"), and the scanner and drift checker
(`services/image-scanner/.../registry.py`, `pipeline/images/drift.py`,
`pipeline/images/registry_auth.py`) know only anonymous or static
username/password registry auth, not ECR's 12-hour tokens. So:
- `PROCESS_RUNTIME_IMAGE`, the scanner image and every `container_images` row
  keep the **public upstream reference** (`ghcr.io/techtraverse/…@sha256:…`).
  The scanner and drift checker read GHCR anonymously, as today.
- The Kubernetes executor rewrites the registry host at launch through a new
  **registry mirror map** setting (`ghcr.io` → `<ecr>/ghcr`, …). The digest is
  unchanged, so approval-by-digest (ADR 0021) is unaffected. (§8, R-8.)
- **Untested: an uncached pull by digest through the pull-through cache.**
  The first pull of a never-cached image is by digest; no AWS statement says
  it resolves through the cache. R-8 tests it before anything relies on it: a
  failure means no process run starts on a fresh cluster.
- Consequence, accepted for the first environment: **user images in a private
  ECR repository are not supported** (they would need an ECR token path from
  the pipeline's workload identity). Recorded as a follow-up.

**Added in this work:** build provenance and SBOM attestations on release
builds (`docker/build-push-action` `provenance`/`sbom`). Cosign signing is
deferred (§12).

## 6. Artifact 2 — the Helm chart

`charts/stac-higher/` in this repo, chart `apiVersion: v2`, works with Helm
3 and 4, published on release as an OCI artifact:
`oci://ghcr.io/techtraverse/charts/stac-higher`, chart version = the release
tag, `appVersion` = the same.

### 6.1 Components

| Component | Kind | Notes |
|---|---|---|
| app | Deployment + Service + HPA | `node app/dist/server/entry.mjs`; ServiceAccount with workload identity |
| pipeline | Deployment, **1 replica**, `strategy: Recreate` (a rolling update would briefly run two schedulers) | worker + scheduler; `PROCESS_EXECUTOR=kubernetes`; ServiceAccount + Role in the runs namespace (K spec §6.3); `/metrics` |
| catalog plane | eoapi-k8s subchart | stac, raster (our titiler image), vector, stac-auth-proxy (our derived image) |
| cube-server | Deployment + Service | `infra/cube-server/`; read-only identity scoped to `assets/*/_cube/*` |
| db bootstrap | Job, runs first | as the RDS master: creates the login roles and the three extensions (§7.2) |
| pgstac migrate | Job, after bootstrap | `pypgstac migrate` from the pipeline image as the owner role (the compose `pgstac-migrate` equivalent), **then**, as the same role, the memberships of `stac_api`/`stac_reader` in the `pgstac_*` roles it created (§7.2) |
| app migrate | Job, after pgstac migrate | `runMigrations()` via a new CLI entry in the app image (§8, R-1), as the owner role. The first-request path stays as a fallback (it is advisory-locked, and the app runs as the owner role, §7.2) |
| demo seed | Job, **off by default** | `pipeline.demo seed / goes-seed / images-seed` against the deployed stack (§8, R-9) |
| ingress | Ingress per public host | className, annotations and hosts from values; nothing AWS-specific in templates |
| runs namespace | Namespace `stac-higher-runs`, default-deny NetworkPolicies, LocalQueue | §6.5 |
| cluster objects | toggle `processRuns.clusterObjects` | ResourceFlavor `cpu-default`, ClusterQueue `process-runs`, WorkloadPriorityClasses, Auto Mode NodePool for runs, ValidatingAdmissionPolicy (label-less Jobs) — reuse #14 (K-6) manifests, do not re-author |
| observability | `ServiceMonitor`s, toggle | pipeline `/metrics`, eoapi's |

**Delivery mechanics must work under both `helm upgrade --install` from CI
and a GitOps controller (Argo CD, Flux)**, because the deployment repo's
tooling is undecided. So: no `lookup`; the three Jobs' ordering and their
prerequisites (ServiceAccount, ConfigMaps, the Secret an ExternalSecret
materialises) must hold under both Helm hook semantics and Argo's hook
mapping. Hook weights, a non-hook Job gated by init containers, or another
pattern are the builder's call; the constraint is the dual compatibility.

Keycloak is **not** packaged. The platform speaks any OIDC IdP (`docs/auth.md`);
a deployment either uses the optional Cognito module (§7.1) or brings its own
IdP.

**eoapi-k8s fit is verified first** (R-6). It must render with: our titiler
and auth-proxy images, the digest-pinned stac-fastapi, an external database
from a Secret, its own pgstac bootstrap **disabled** (ADR 0001 — our hook
Job owns pgstac migrations), and its bundled Crunchy PGO off. If it fights
any of these, the fallback is our own templates for those four services,
recorded in the slice PR; no other part of this spec changes.

### 6.2 Values contract

The values file is the deployment's whole interface. Its shape:

```yaml
global:
  imageRegistry: ""          # e.g. the ECR pull-through prefix
  imageDigests: {}           # name -> sha256:..., from the release manifest (§8)
publicUrls:                  # every browser-visible URL (§8 R-1)
  app: https://…
  catalog: https://…         # stac-auth-proxy
  titiler: https://…
  tipg: https://…
  cubeServer: https://…
  objectStore: https://…     # S3 regional endpoint, for presigned URLs
aws:
  region: us-east-1
  bucket: …
  processRunRoleArn: …       # the STS role the pipeline mints run credentials from
  s3PrefixListCidrs: []      # for the runs NetworkPolicy (§6.5)
database:
  host: …
  existingSecret: …          # master credentials (§6.4)
auth:
  mode: oidc                 # chart refuses dev-bypass unless allowDevBypass: true
  issuer: …
  clientId: …
identity:
  mode: pod-identity         # or irsa (§9)
  roleArns: { app: …, pipeline: …, titiler: …, cubeServer: … }  # irsa only
secrets:
  mode: external-secrets     # or existing
  …
config:
  hardwareProfiles: {...}    # rendered into a ConfigMap -> PROCESS_HARDWARE_PROFILES_FILE
  imagePolicy: {...}         # -> PROCESS_IMAGE_POLICY_FILE
ingress: { className: …, annotations: {}, hosts: {…} }
processRuns: { clusterObjects: true, cpuQuota: {cpu: …, memory: …} }
demoSeed: { enabled: false }
```

Names are the builder's; the **categories** are the contract. The Terraform
module emits an output shaped like the AWS-derived half of this
(`helm_values`, YAML), so the deployment repo passes one into the other
without hand-copying ARNs.

### 6.3 Config files as values

`infra/hardware-profiles/` and `infra/image-policy/` are baked into **both**
the app and the pipeline images today, and both read them. The chart renders
the deployment's documents from values into ConfigMaps mounted into both
Deployments at the paths `PROCESS_HARDWARE_PROFILES_FILE` and
`PROCESS_IMAGE_POLICY_FILE` name; the app caches the file per process, so a
ConfigMap change rolls both Deployments (checksum annotation). The chart
ships an `eks-cpu` hardware-profile default for the first environment, whose
`max_queue_wait_seconds` fits the credential budget (§8).

### 6.4 Secrets

Every secret the platform reads — the per-role DB credentials (§7.2),
`SESSION_SECRET`, `CATALOG_BFF_SHARED_SECRET`, `INTERNAL_API_TOKEN`,
`CREDENTIALS_MASTER_KEY`, `PROXY_AUTH_TOKEN`, the scanner's Docker Hub
credentials (`REGISTRY_DOCKERHUB_USER`/`REGISTRY_DOCKERHUB_TOKEN`, optional)
— reaches pods through named
Kubernetes Secrets. (The app's OIDC login is a public PKCE client; no client
secret is read.) The builder re-audits this list against the code.
`secrets.mode`:
- `external-secrets`: the chart renders `ExternalSecret`s from Secrets
  Manager names (created by the Terraform `secrets` module).
- `existing`: the chart references Secrets the deployment created.

The chart never renders a secret value from values.

### 6.5 Network policy

`stac-higher` namespace: default-deny ingress; allow from the ingress
controller to the public services, and service-to-service as compose wires
them today. Egress open (destinations and NODD ingest need the internet;
the egress lockdown is #68).

`stac-higher-runs` namespace (K spec §6.2): default-deny; `isolated` runs
may reach DNS and S3 only. S3 via the gateway endpoint is S3's public IP
ranges, so the policy takes `aws.s3PrefixListCidrs`, which the Terraform
module reads from the region's `com.amazonaws.<region>.s3` managed prefix
list. Scan runs get a separate policy allowing 443 egress (registry pulls),
the cloud form of compose's `scanner-egress` network.

## 7. Artifact 3 — Terraform/OpenTofu modules

`infra/terraform/modules/` in this repo. Compatible with **OpenTofu ≥ 1.10
and Terraform ≥ 1.10** (the common language subset; CI runs both
`validate`s). `hashicorp/aws` provider `~> 6.0`. Modules declare
`required_providers` but **never configure a provider, backend or account**.
Consumed by git tag:
`git::https://github.com/TechTraverse/stac-higher.git//infra/terraform/modules/stac-higher?ref=vX.Y.Z`.

### 7.1 Modules

| Module | Creates | Takes as input |
|---|---|---|
| `stac-higher` | composition of the below + the `helm_values` output | everything the children take |
| `kms` | CMKs for RDS, S3, Secrets Manager (one or several — builder's call) | — |
| `storage` | the platform bucket: SSE-KMS, public access blocked, **versioning off** (ADR 0011: deleted bytes must go), CORS for browser presigned PUT/GET from `cors_allowed_origins`, TLS-only bucket policy | name prefix, origins |
| `database` | RDS PostgreSQL (PostGIS + btree_gist available), parameter group seeded from eoapi-cdk's pgstac tuning, `manage_master_user_password` (Secrets Manager), KMS, backups, deletion protection toggle, Multi-AZ toggle (off is fine for test) | VPC id, subnet ids, allowed security groups, instance class |
| `identity` | per-workload IAM roles — app (presign: Get/Put on the bucket), pipeline (S3 read/write/delete, `sts:AssumeRole` on the run role, KMS), titiler (S3 read), cube-server (S3 read/list on `assets/*/_cube/*`); the **process-run role** the pipeline assumes with a session policy (trust: the pipeline role only); Pod Identity associations **or** IRSA trust (§9) | cluster name, OIDC provider (irsa), namespace + SA names |
| `secrets` | Secrets Manager entries with generated values: one password per DB login role (§7.2), `SESSION_SECRET`, the BFF secret, the internal token, the credentials master key, the proxy token | — |
| `registry` | ECR pull-through cache rules (ghcr, docker hub, quay — each optional), repository creation templates (scan on push, KMS) | upstream credential secret ARNs |
| `demo-destination` *(optional)* | a second bucket standing in for a customer's delivery target, and an IAM user whose policy allows writes to that bucket only; its access key goes into a Secrets Manager entry the demo seed reads and seals into an S3 connection, exactly as a customer would enter theirs (lead decision 2026-11-02). The key sits in Terraform state; acceptable for a test environment | name prefix |
| `cognito` *(optional)* | user pool, groups matching the claims mapping, public app client with PKCE, hosted domain; used with the audience claim set to `client_id` (§8) | callback URLs |

Not in scope: VPC, EKS, Route 53 zones, ACM certificates (inputs to the
ingress via values), SQS (waits for the SQS backend), WAF.

### 7.2 Database roles — a new, minimal role model, created by the chart

Today compose runs **everything as one superuser** (`username`); the only
other login is `cube_server` (migration 033). This is new design, kept as
close to today as AWS allows:

| Login role | Used by | Grants |
|---|---|---|
| `stac_higher_owner` | app, pipeline, both migrate Jobs | owns the `stac_higher` schema. For pgstac it has **owner rights by membership**: pypgstac 0.9.11's migration creates `pgstac_admin`/`pgstac_read`/`pgstac_ingest`, grants `pgstac_admin` to the migrating role, and makes `pgstac_admin` the owner of every pgstac object (`pgstac_admin_owns()`). That membership is what lets the app's migrations attach `item_events_capture_trg` to `pgstac.items`. `CREATEROLE`, needed by the pgstac migration itself and by migration 033; **not** superuser |
| `stac_api` | stac-fastapi | member of pgstac's own `pgstac_ingest` (transactions enabled) |
| `stac_reader` | titiler, tipg | member of `pgstac_read`; tipg also `SELECT` on the vector tables it serves |
| `stac_proxy_policy` | the enforced auth-proxy's `POLICY_DATABASE_URL` | read-only, the role ADR 0015 says the cloud must provision — exact grants from ADR 0015 |
| `cube_server` | cube-server | member of `stac_higher_cube_reader` (migration 033) |

- The **RDS master** is used only by the db-bootstrap Job, which reads the
  current secret each run, so Secrets Manager's master-password rotation
  breaks nothing.
- **Extensions are pre-created by the bootstrap Job as master.** The pgstac
  migration runs `CREATE EXTENSION IF NOT EXISTS postgis / btree_gist /
  unaccent`; a non-superuser on RDS can do that only as the database owner
  (trusted extensions). Pre-creating them as master (our call, over making
  `stac_higher_owner` the database owner) turns the migration's statements
  into no-ops and keeps the owner role smaller.
- **Ordering on Postgres ≥ 16.** `CREATEROLE` lets a role grant membership
  only in roles it created or holds ADMIN on. `stac_higher_owner` creates the
  `pgstac_*` roles during the pgstac migration, so the `stac_api` →
  `pgstac_ingest` and `stac_reader` → `pgstac_read` memberships are granted
  **after** that migration, by the owner (the pgstac migrate Job's second
  step, §6.1), not by the bootstrap Job, which runs first and as master. The
  same holds for `cube_server` → `stac_higher_cube_reader` (migration 033,
  run by the owner).
- Per-role passwords are generated by the Terraform `secrets` module and
  reach the Job and the workloads through §6.4.
- Splitting the app's runtime role from the migration owner is deferred;
  it would end the first-request migration fallback and is not needed for a
  test environment.
- Created by a chart Job, not Terraform's `postgresql` provider, which
  would need network reach to a private RDS from the deployment's CI runner.
  The Job is idempotent and replaces compose's `cube-db-role` one-shot.

**TLS to RDS.** RDS PostgreSQL enforces TLS by default (`rds.force_ssl`) and
no client configures TLS today. The parameter group keeps it on; every client
(node-postgres, psycopg, pypgstac, titiler, tipg, stac-fastapi, the proxy,
the cube-server) connects with certificate verification against the RDS CA
bundle, which the deployment supplies as a value (`database.caBundle`, a
ConfigMap) because the bundle differs between commercial and GovCloud. Code
audit in §8.

## 8. Code changes the artifacts depend on

These are not IaC, but without them the published artifacts cannot run in
AWS. Each becomes a slice or folds into one (§12).

- **Browser-visible URLs are build-time today.** `PUBLIC_BUILTIN_CATALOG_URL`
  (`app/src/stores/catalogStore.ts`), `PUBLIC_TITILER_URL` and
  `PUBLIC_TIPG_URL` (`app/src/lib/serving/urls.ts`, read from
  `import.meta.env` in React islands) and `PUBLIC_PROCESS_NETWORK_MAX` are
  inlined by Vite, so one published image cannot serve two deployments.
  Move every browser-visible setting (including the cube-server URL) to
  runtime configuration delivered by the server. The builder audits every
  `PUBLIC_*` and `import.meta.env` read.
- **Static S3 credentials are assumed.** `app/src/lib/storage/client.ts`
  always passes `accessKeyId`/`secretAccessKey` (default `minioadmin`); the
  pipeline's `STAGING_S3_*` keys default to `minioadmin`; the cube-server
  **requires** `CUBE_S3_ACCESS_KEY_ID`/`CUBE_S3_SECRET_ACCESS_KEY`
  (`infra/cube-server/stac_higher_cube_server/settings.py`). When unset
  (empty), each must fall back to the SDK default credential chain (Pod
  Identity / IRSA). Same audit for titiler (GDAL) and the pipeline's
  Icechunk/obstore readers. **Risk:** verify that GDAL's, obstore's and
  Icechunk's container-credential support covers Pod Identity's token file
  in the pinned versions; IRSA (web identity) is the fallback the identity
  toggle exists for.
- **STS role chaining caps run credentials at 1 hour.** The pipeline itself
  runs on a role, so its `AssumeRole` into the process-run role is role
  chaining, which AWS caps at 3600 s. Today the pipeline asks for
  `timeout_seconds + process_credential_grace_seconds`
  (`services/pipeline/src/pipeline/process/credentials.py:179`); K-4 (#12,
  K spec §5.4) adds the profile's `max_queue_wait_seconds`. The app allows
  `timeout_seconds` up to 86 400. So:
  - a new deployment setting, `PROCESS_MAX_CREDENTIAL_SECONDS` (unset = no
    limit, as today; 3600 on AWS), read by **both** app and pipeline;
  - the app **refuses a process-revision deploy** whose
    `max_queue_wait_seconds + timeout_seconds + grace` exceeds it (422 with
    a clear `code`), using the profile the revision names;
  - the pipeline refuses to mint above it (a run error, never a silent
    clamp that would expire mid-run);
  - the chart's `eks-cpu` profile carries a `max_queue_wait_seconds` that
    leaves room for GeoColor's timeout (the builder measures it).

  Longer runs on AWS are a later design. **Untested: whether the cap also
  applies under IRSA** (a web-identity session calling `AssumeRole`). AWS
  defines role chaining as using one role's session to assume another, which
  suggests it does; the PR #125 review recalls otherwise. R-11 settles it
  with one call; if the cap does not apply, the IRSA toggle lifts it and the
  later design shrinks to "run the pipeline on IRSA". **Also** re-measure I-90
  (the 2048-char session-policy cap) against real STS.
- **The bearer-token audience claim is configurable** (lead decision
  2026-11-02). The app (`app/src/lib/auth/bearer.ts`, `AUTH_BEARER_AUDIENCES`)
  and the enforced proxy (`ALLOWED_JWT_AUDIENCES`) both match a token's `aud`.
  Cognito access tokens carry no `aud`; they carry `client_id`. A new setting
  names the claim to match (`aud` default, or `client_id`), read by the app
  and applied at the proxy. Whether the proxy half is an upstream
  stac-auth-proxy option or a check in our derived image (`infra/proxy-policy/`)
  is the builder's finding. This makes the platform work with Cognito and with
  any IdP that puts the audience in `aud`.
- **Registry mirror map** in the Kubernetes executor (§5): a setting mapping
  upstream registry hosts to mirror prefixes, applied to the image reference
  at Job creation, digest preserved.
- **TLS to RDS** (§7.2): every database client accepts a CA bundle and a
  verifying `sslmode`; node-postgres needs its `ssl.ca` set explicitly.
- **App migrations as a command.** A CLI entry in the app image that runs
  `runMigrations()` and exits, for the hook Job.
- **The auth-proxy image is not published** (§5).
- **The Kubernetes executor path**: #11 → #12 → #13 → #14 (K-3…K-6). The
  demo's extractors and GeoColor are process runs, and EKS Auto Mode has no
  Docker daemon, so these are **hard prerequisites for the demo** though not
  for building the artifacts.
- **Demo seed against a deployed stack.** `pipeline.demo` assumes the compose
  stack: it can build the runtime image, it knows compose hostnames, and
  `goes-seed --deliver` seals MinIO keys into its deliver connection. It
  needs a mode that runs as an in-cluster Job against configured endpoints
  with `--no-build-image`, sealing the `demo-destination` key instead.
- **Credentials master key.** The test environment uses a Secrets Manager
  value for `CREDENTIALS_MASTER_KEY` (acceptable here). The KMS-backed
  `EncryptionProvider` (`docs/ISSUES.md`) is not a blocker for this
  environment and stays a separate item.

## 9. GovCloud portability — the cheap rules

Adopted because each costs almost nothing now:
- Every ARN built with `data.aws_partition`; every service principal and
  endpoint suffix from `data.aws_partition.dns_suffix`. No literal `arn:aws:`.
- No dependency on ECR Public, Lambda@Edge, CloudFront-only features, or any
  service absent from `us-gov-west-1`/`us-gov-east-1`.
- Workload identity is a toggle: **Pod Identity** (default) or **IRSA**.
  IRSA works everywhere and with every SDK; it is the fallback if a region or
  a library lacks Pod Identity support.
- Hardware profiles are per-deployment documents already (K spec §11); the
  default shipped profile is CPU-only.
- Cognito is optional; Cognito feature parity in GovCloud is not assumed.

Not done now (costly, deferred): FIPS endpoints throughout, a GovCloud CI
leg, GovCloud-specific instance families.

## 10. Validation in this repo's CI (no AWS account)

A new workflow job (or jobs) on every PR touching `charts/`, `infra/terraform/`
or the example:
- `tofu fmt -check`, `tofu validate` and `terraform validate` on every module
  and on the example; `tflint` with the AWS ruleset; `trivy config` (already in
  use) over both trees.
- `helm lint`; `helm template` of the example values; **kubeconform** against
  Kubernetes 1.34 schemas plus the CRDs the chart uses (Kueue, External
  Secrets, Karpenter NodePool, ServiceMonitor).
- A render test asserting the anti-goals: dev-bypass refused without the
  override, no Secret with literal data, every image reference digest-pinned
  when `global.imageDigests` is set.

**The example** (`infra/examples/aws-eks/`): a root module calling
`stac-higher` with placeholder values (`000000000000`, `example.com`) and a
values file using its `helm_values` output — the template a deployment repo
copies. It is validated, never applied.

A kind-cluster **install** smoke (chart installed against kind with Silo and
a throwaway pgstac) is valuable and builds on #14's kind leg; it is a later
slice, not a gate for the first release.

## 11. Releases

On a `v*` tag (extending `release.yml` / `containers.yml`):
1. Images build and push (exists), with provenance + SBOM attestations.
2. The chart is packaged with `version`/`appVersion` = the tag and pushed to
   `oci://ghcr.io/techtraverse/charts/stac-higher`.
3. A **release manifest** (`release-manifest.json`: every image name → digest,
   the chart reference, the module git ref) is attached to the GitHub release.
   A deployment pins from it; the chart's `global.imageDigests` takes it
   directly.

The module needs no publishing step; the tag is its version.

## 12. Slices (proposed queue `deploy artifacts (R)`)

| # | Slice | Depends on |
|---|---|---|
| R-1 | App: runtime config for every browser-visible URL; S3 default credential chain; RDS TLS (node-postgres); `PROCESS_MAX_CREDENTIAL_SECONDS` deploy refusal; configurable audience claim; migrate CLI | — |
| R-2 | Pipeline + cube-server: S3 default credential chain (boto3, obstore, Icechunk); RDS TLS (psycopg, pypgstac); credential cap at mint; confirm the titiler base image's GDAL is ≥ 3.9 (Pod Identity token-file support, OSGeo/gdal#9877) | — |
| R-3 | Publish the auth-proxy image (with the configurable audience claim if it lands in our derived image); provenance + SBOM; release manifest | — |
| R-4 | Terraform `kms`, `storage`, `secrets`, `registry` + the CI validation job + example skeleton | — |
| R-5 | Terraform `database` (TLS on, pgstac tuning), `identity` (Pod Identity + IRSA), `cognito`, `demo-destination`, `helm_values` output | R-4 |
| R-6 | Chart skeleton: app, pipeline, db-bootstrap + migrate Jobs (§7.2 roles), config ConfigMaps into both Deployments, secrets modes, ingress, `stac-higher` NetworkPolicies, Helm + Argo compatibility, render tests | R-1, R-2 |
| R-7 | Chart: eoapi-k8s catalog plane (fit check first, §6.1) + cube-server | R-6 |
| R-8 | Chart: runs namespace, pipeline RBAC, runs NetworkPolicies, Kueue + NodePool cluster objects from #14; registry mirror map in the Kubernetes executor; test an uncached by-digest pull through the ECR cache (§5) | R-6, #13, #14 |
| R-9 | Release workflow: chart push to GHCR OCI; first `v0.x` tag | R-3, R-6 |
| R-10 | Demo seed as an in-cluster Job, sealing the `demo-destination` key into the deliver connection | R-6, R-5 |
| R-11 | **Lead-only**: first deploy rehearsal from a deployment repo into the test account; GOES loop + GeoColor + cube demo; test whether the 1 h cap applies under IRSA (§8); record gaps | all, cluster |

R-6 sets `PROCESS_EXECUTOR=kubernetes` and the `eks-cpu` profile's
`backend.kubernetes` block against the K spec's contract (§6, §3.4); #13
(K-5) is not merged, so R-6 accepts rework if K-5 changes that contract.

**#16 (K-8)** is re-scoped: the cluster half moves to the cluster repo, the
CPU Kueue/NetworkPolicy/RBAC/identity half is R-5/R-8, and #16 keeps the
**GPU leg** (GPU NodePools, `gpu-*` profiles, CUDA image) — still blocked,
still after the first environment.

Deferred, filed as issues or `docs/ISSUES.md` entries: cosign signing; the
kind install smoke; the KMS `EncryptionProvider`; FIPS/GovCloud CI leg; SQS
module (with the SQS backend); per-group Kueue queues (I-96).

## 13. Open questions for the lead

- **Q1.** Who owns the app-specific cluster-scoped objects (Kueue
  ClusterQueue/flavors/priority classes, the runs NodePool, the admission
  policy): this chart (default, toggle on) or the cluster repo? Needs the
  cluster team.
- **Q2 — settled 2026-11-02.** The IdP is the deployment's choice; the
  optional `cognito` module is built, and the token audience claim is made
  configurable (`aud` or `client_id`, §8).
- **Q3 — settled 2026-11-02.** Demo destinations deliver to an S3 bucket in
  the test account through a scoped IAM user key entered as a connection
  credential (`demo-destination`, §7.1). No new connection code.

## 14. Calls made without the lead — confirm or overturn

- eoapi-cdk not used at all; eoapi-k8s used as a subchart, with a
  fall-back-to-own-templates exit.
- One chart (`stac-higher`), not one per service.
- Terraform modules consumed by git ref, not a registry.
- Database roles via a chart Job, not the `postgresql` provider.
- Keycloak not packaged.
- Pipeline at 1 replica (scheduler HA is not designed).
- Bucket versioning off (ADR 0011).
- Pod Identity default, IRSA toggle.
- ECR pull-through cache as the image path; GHCR remains the publish target.
- The test environment's credentials master key lives in Secrets Manager.
- Run credentials bounded at 1 h on AWS by an explicit refusal (deploy and
  mint), not redesigned now.
- A minimal new DB role model (§7.2): app and pipeline share an owner role;
  the master is used only by the bootstrap Job.
- RDS TLS stays enforced; the deployment supplies the CA bundle.
- Image rows keep upstream references; the executor maps the pull to the ECR
  mirror. User images in a private ECR repository are not supported yet.
- The chart must work with both `helm upgrade --install` and Argo CD/Flux.

## 15. Risks

- **eoapi-k8s fit** (§6.1) — mitigated by the fallback.
- **Workload-identity support in obstore/Icechunk** (§8) — IRSA fallback.
  GDAL supports Pod Identity's token file from 3.9 (OSGeo/gdal#9877); R-2
  confirms the titiler image's version.
- **ECR pull-through by digest** (§5) — untested until R-8.
- **Role-chaining 1 h cap** limits long process runs on AWS (§8) — explicit
  refusal now, design later.
- **Kueue + Auto Mode** — the K spec §7.3 coupling (quota = NodePool limits)
  is only as good as the toggle owner in Q1.
- **No account in CI** — everything is validated by rendering, not applying,
  until R-11; R-11 will find things rendering cannot.
