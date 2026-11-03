# R-6 · Helm chart skeleton: app, pipeline, db-bootstrap + migrate Jobs, config, secrets, ingress, NetworkPolicies, render tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub #142 (epic #136).

**Goal:** Ship the `stac-higher` Helm chart at `infra/helm/stac-higher/`: the app and pipeline workloads, the three database Jobs that create the §7.2 role model and run both migrations, config and secrets plumbing, the app Ingress and the `stac-higher` namespace NetworkPolicies. It must install with `helm upgrade --install` and Argo CD/Flux alike, and be proven by render tests, lint, kubeconform and trivy with no cluster.

**Architecture:** One chart with a binding values contract (`values.yaml` + `values.schema.json` + one cross-field `validate` template). Non-secret config goes into ConfigMaps consumed by both Deployments through `envFrom` and one mounted file ConfigMap; secrets reach pods only through named Kubernetes Secrets, either materialised by External Secrets Operator from the Terraform `secrets` module's names or supplied by the deployment. The three DB Jobs are `pre-install,pre-upgrade` Helm hooks that carry matching Argo CD `PreSync` hook annotations. Every object a hook Job reads is itself a hook copy at a lower weight, so ordering holds under both tools without `lookup`. Render tests are vitest suites in the app workspace that shell out to `helm template` through a small Node harness. The SQL the Jobs run is pure SQL executed by a tiny psycopg runner, tested by pytest against the CI pgstac service.

**Tech Stack:** Helm 3.22 / 4.3 (chart `apiVersion: v2`, JSON Schema draft-07), External Secrets Operator `external-secrets.io/v1`, Prometheus Operator `monitoring.coreos.com/v1` (optional), vitest + `yaml` 2.9, Python 3.12 + psycopg 3 (the pipeline image), kubeconform 0.8.0, trivy, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §4.1, §5, §6 (all), §7.2, §8, §10, §12 (R-6 row); K spec `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §3.2, §3.4, §5.4, §6; ADR 0024. Image contract: `docs/releases.md`. Secret keys: `infra/terraform/modules/secrets/README.md`. Mirror map: `infra/terraform/modules/registry/README.md`. Env: `docs/backend.md` "App environment" and "Deployment: S3 credentials and database TLS (R-2)", `docs/auth.md`, `services/pipeline/README.md` "Environment contract".

## Global Constraints

### Layout and chart identity

- The chart lives at **`infra/helm/stac-higher/`** (lead ruling). Spec §6 and §10, and issue #142, say `charts/stac-higher/`; Task 10 corrects the spec text. The published OCI ref stays `oci://ghcr.io/<owner>/charts/stac-higher` (`docs/releases.md`), and R-9 packages from the new path.
- `Chart.yaml`: `apiVersion: v2`, `name: stac-higher`, `type: application`, `version: 0.1.0`, `appVersion: "0.1.0"`, `kubeVersion: ">=1.34.0-0"`, `sources: [https://github.com/TechTraverse/stac-higher]`. R-9 sets `version`/`appVersion` from the tag.
- `.helmignore` excludes `tests/`. Chart files that templates read live under `files/`.
- R-6 renders **no** catalog plane, cube-server, runs namespace, RBAC in the runs namespace, Kueue or NodePool objects, and no demo-seed Job: those belong to R-7, R-8 and R-10. Keys those slices consume but R-5 already emits are declared in `values.yaml` with a "consumed by R-x" comment, so R-5's output validates today.

### Values contract (binding; R-5's `helm_values` output is accepted verbatim)

R-5's committed plan emits `helm_values` with the keys marked **R-5** below, under exactly these paths. The chart accepts them as is.

R-5 **omits** an optional section when it is off, never `null`, because a `null` in a values file deletes the chart default. The chart therefore treats a missing key and its default the same way. An explicit `null` from a deployment means "unset" only where noted.

`values.yaml` carries every key below with this default and a one-line comment. `null` means required, which the schema or `validate` enforces.

| Key | Default | Notes |
|---|---|---|
| `nameOverride`, `fullnameOverride` | `""` | standard |
| `global.imageRepository` | `ghcr.io/techtraverse/stac-higher` | upstream image = `<imageRepository>-<key>` |
| `global.imageTag` | `""` | `""` → `.Chart.AppVersion` |
| `global.imageDigests` | `{}` | release manifest `images.*.digest`, keys verbatim (`docs/releases.md` "Feeding the chart") |
| `global.imageRegistry` | `""` | **R-5**: the GHCR mirror prefix (`<acct>.dkr.ecr.<region>.<suffix>/ghcr`). Replaces the leading `ghcr.io` of every image the chart **pulls** |
| `registry.host` | `""` | **R-5**: the ECR registry host. Informational in R-6 |
| `registry.mirrorPrefixes` | `{}` | **R-5**: `{ghcr.io, docker.io, quay.io}` → prefix (the registry module's `mirror_prefixes`). The pull rewrite falls back to it when `global.imageRegistry` is blank. R-8 hands it to the executor |
| `aws.partition` | `aws` | **R-5**. Informational in R-6 |
| `aws.region` | `us-east-1` | **R-5**. → `AWS_REGION`, `AWS_DEFAULT_REGION`, `S3_REGION`, `STAGING_S3_REGION` |
| `aws.bucket` | `null` | **R-5**. → `S3_BUCKET`, `STAGING_BUCKET` |
| `aws.kmsKeyArn` | `""` | **R-5**. Informational: the bucket's default SSE-KMS applies without a client header |
| `aws.processRunRoleArn` | `null` | **R-5**. → `PROCESS_STS_ROLE_ARN` |
| `aws.processMaxCredentialSeconds` | `3600` | **R-5**. Integer ≥ 900. → `PROCESS_MAX_CREDENTIAL_SECONDS` in the shared ConfigMap, read by app and pipeline. A deployment `null` deletes it, which means no cap |
| `aws.s3PrefixListCidrs` | `[]` | **R-5**. Consumed by R-8 (runs NetworkPolicy) |
| `publicUrls.app` / `.objectStore` | `null` | **R-5** (`objectStore` = `https://s3.<region>.<suffix>`). Required, `^https?://`. `app` has no path. `objectStore` → the app's `S3_ENDPOINT` only |
| `publicUrls.catalog` / `.titiler` / `.tipg` / `.cubeServer` | `null` | deployment-supplied (R-5 does not emit them; flagged). Required |
| `objectStore.forcePathStyle` | `false` | |
| `objectStore.internalEndpoint` | `""` | → `STAGING_S3_ENDPOINT` **and** `PROCESS_RUN_S3_ENDPOINT`, always rendered, even when empty. It must stay blank on AWS: the pipeline's STS client derives its endpoint from it (R-5's I-152), and blank → `None` → the AWS default. **Never derived from `publicUrls.objectStore`.** It is set for MinIO only |
| `database.host` / `.name` | `null` | **R-5** |
| `database.port` | `5432` | **R-5** |
| `database.masterSecretArn` | `""` | **R-5**: the RDS-managed master secret (JSON `username`/`password`). The hook ExternalSecret's remote key in `external-secrets` mode, where it is required |
| `database.caCertIdentifier` | `""` | **R-5** (e.g. `rds-ca-rsa2048-g1`). Informational: names the bundle a deployment must supply |
| `database.sslMode` | `verify-full` | enum `verify-full` \| `disable` (dev only) |
| `database.caBundle.existingConfigMap` | `""` | deployment-supplied ConfigMap. With `verify-full`, exactly one of this or `pem` is set (R-5 emits neither; flagged) |
| `database.caBundle.key` | `ca-bundle.pem` | key inside `existingConfigMap` |
| `database.caBundle.pem` | `""` | PEM text; the chart renders the ConfigMap |
| `auth.mode` | `oidc` | **R-5** (cognito on). enum `oidc` \| `bypass` |
| `auth.allowDevBypass` | `false` | `bypass` without it fails to render |
| `auth.issuer` / `.clientId` | `null` | **R-5** (cognito on). Required when `oidc` → `OIDC_ISSUER` / `OIDC_CLIENT_ID` |
| `auth.issuerInternal` | `""` | → `OIDC_ISSUER_INTERNAL` |
| `auth.audiences` | `[stac-higher]` | **R-5** `[<clientId>]`. Comma-joined → `AUTH_BEARER_AUDIENCES` |
| `auth.audienceClaim` | `aud` | **R-5** `client_id`. enum `aud` \| `client_id` → `AUTH_BEARER_AUDIENCE_CLAIM` |
| `auth.claimsMapping` | `null` | **R-5** (cognito on). Object → `AUTH_CLAIMS_MAPPING` (compact JSON) |
| `auth.hostedDomain` | `""` | **R-5** (cognito on). Informational (no app env reads it) |
| `auth.devIdentity` | `null` | object → `DEV_AUTH_IDENTITY` (bypass only) |
| `identity.mode` | `pod-identity` | **R-5**. enum `pod-identity` \| `irsa` |
| `identity.namespace` | `stac-higher` | **R-5**. The release namespace **must** equal it, or render fails: the Pod Identity associations name it |
| `identity.serviceAccounts.app` / `.pipeline` / `.titiler` / `.cubeServer` | `stac-higher-app` / `stac-higher-pipeline` / `stac-higher-titiler` / `stac-higher-cube-server` | **R-5**. The chart names its ServiceAccounts **only** from these (not from the release name). `titiler`/`cubeServer` are consumed by R-7 |
| `identity.roleArns.app` / `.pipeline` / `.titiler` / `.cubeServer` | `""` | **R-5**. `app`/`pipeline` are required when `irsa` → `eks.amazonaws.com/role-arn`. `titiler`/`cubeServer` are consumed by R-7 |
| `secrets.mode` | `external-secrets` | **R-5**. enum `external-secrets` \| `existing` |
| `secrets.arns` | ten keys, each `""` | **R-5**: the `secrets` module's ten keys verbatim: `db_stac_higher_owner`, `db_stac_api`, `db_stac_reader`, `db_stac_proxy_policy`, `db_cube_server`, `session_secret`, `catalog_bff_shared_secret`, `internal_api_token`, `credentials_master_key`, `proxy_auth_token`. Used as ESO `remoteRef.key`. All but `proxy_auth_token` are required in `external-secrets` mode |
| `secrets.dockerHubArn` | `""` | optional, JSON `username`/`token` (deployment-supplied) |
| `secrets.externalSecrets.storeRef.name` | `""` | required in `external-secrets` mode (the cluster's store, deployment-supplied) |
| `secrets.externalSecrets.storeRef.kind` | `ClusterSecretStore` | enum `ClusterSecretStore` \| `SecretStore` |
| `secrets.externalSecrets.refreshInterval` | `1h` | |
| `secrets.existing.platform` / `.dbMaster` / `.dbOwner` / `.dbStacApi` / `.dbStacReader` / `.dbProxyPolicy` / `.dbCubeServer` | `""` | all required in `existing` mode |
| `secrets.existing.dockerHub` | `""` | optional |
| `demoDestination.bucket` / `.region` / `.credentialsSecretArn` | `""` | **R-5** (when on). Consumed by R-10 |
| `processRuns.namespace` | `stac-higher-runs` | R-8 creates it |
| `processRuns.networkMax` | `isolated` | → `PROCESS_NETWORK_MAX` (shared ConfigMap: app + pipeline) |
| `processRuns.credentialGraceSeconds` | `300` | → `PROCESS_CREDENTIAL_GRACE_SECONDS` (shared ConfigMap: app + pipeline, one value) |
| `processRuns.clusterObjects` | `true` | consumed by R-8 |
| `config.hardwareProfiles` | `null` | `null` → the chart's `files/hardware-profiles/eks-cpu.json` |
| `config.imagePolicy` | `null` | `null` → the chart's `files/image-policy/default.json` |
| `ingress.enabled` | `true` | |
| `ingress.className` | `""` | |
| `ingress.annotations` | `{}` | on every chart Ingress |
| `ingress.tlsSecretName` | `""` | set → `spec.tls` (cert-manager). The ALB uses annotations instead |
| `ingress.app.annotations` | `{}` | merged over `ingress.annotations` |
| `networkPolicy.enabled` | `true` | |
| `networkPolicy.appIngressFrom` | `null` | `null` → the app port is open to any source. Otherwise a list of NetworkPolicy peers |
| `networkPolicy.metricsFrom` | `[]` | peers allowed to reach pipeline `:8083` |
| `app.replicas` | `2` | omitted when the HPA is on |
| `app.autoscaling.enabled` / `.minReplicas` / `.maxReplicas` / `.targetCPUUtilizationPercentage` | `false` / `2` / `6` / `70` | |
| `app.resources` | requests `cpu: 250m, memory: 512Mi`, limits `memory: 1Gi` | |
| `app.readOnlyRootFilesystem` | `true` | escape hatch (I-161) |
| `app.builtinCatalogInternalUrl` | `""` | → `BUILTIN_CATALOG_URL`. Its host is added to `SAFE_FETCH_ALLOW_HOSTS`. R-7 sets it |
| `app.safeFetchAllowHosts` | `[]` | extra hosts |
| `app.env` | `{}` | extra non-secret env |
| `app.podAnnotations`, `app.nodeSelector`, `app.tolerations`, `app.affinity` | `{}`, `{}`, `[]`, `{}` | |
| `pipeline.resources` | requests `cpu: 500m, memory: 1Gi`, limits `memory: 2Gi` | README memory envelope ≈ 735 MiB |
| `pipeline.readOnlyRootFilesystem` | `true` | |
| `pipeline.egressAllowHosts` | `[]` | → `EGRESS_ALLOW_HOSTS` |
| `pipeline.env`, `pipeline.podAnnotations`, `pipeline.nodeSelector`, `pipeline.tolerations`, `pipeline.affinity` | as for `app` | |
| `pipeline.metrics.serviceMonitor.enabled` / `.interval` / `.labels` | `false` / `30s` / `{}` | |
| `dbJobs.backoffLimit` / `.activeDeadlineSeconds` | `2` / `900` | |
| `dbJobs.resources` | requests `cpu: 100m, memory: 256Mi`, limits `memory: 1Gi` | |
| `dbJobs.nodeSelector`, `dbJobs.tolerations`, `dbJobs.affinity` | `{}`, `[]`, `{}` | |
| `demoSeed.enabled` | `false` | consumed by R-10 |

Spec §6.2 keys R-5 does not emit, kept in the chart (the deployment supplies them; flagged to R-5):
- `publicUrls.{catalog,titiler,tipg,cubeServer}`;
- a CA bundle source (`database.caBundle.*`), because `caCertIdentifier` alone cannot be mounted;
- `secrets.externalSecrets.storeRef`, `config.*`, `ingress.*`, `processRuns.{clusterObjects,cpuQuota}` (R-8) and `demoSeed.enabled`.

Spec §6.2's `database.existingSecret` became `database.masterSecretArn` / `secrets.existing.dbMaster`.

Schema (`values.schema.json`, draft-07):
- types and enums for every key above;
- `"type": "string", "minLength": 1` for the unconditional `null`-default keys: `publicUrls.*`, `aws.bucket`, `aws.processRunRoleArn`, `database.host`, `database.name`;
- `"additionalProperties": false` on `publicUrls`, `objectStore`, `database`, `database.caBundle`, `aws`, `registry`, `identity`, `identity.serviceAccounts`, `identity.roleArns`, `secrets.arns`, `secrets.existing` and `demoDestination`. These are exactly R-5's key sets plus the chart's, so typos fail and R-5's output still validates;
- other objects stay open (R-7/R-8 add sections).

Conditional rules live in `templates/validate.yaml`, which fails with a message starting with the offending key path.

### Secrets

- **The chart never renders a `kind: Secret`.** There are two modes only, `external-secrets` and `existing`. The brief's `inline` mode is dropped: spec §6.4 and the issue forbid literal secret values from values. Dev uses `existing` with the README's `kubectl create secret` recipe.
- Kubernetes Secret layouts (both modes):
  - **db secrets** (`dbMaster`, `dbOwner`, `dbStacApi`, `dbStacReader`, `dbProxyPolicy`, `dbCubeServer`): keys `username`, `password`.
  - **platform**: keys `SESSION_SECRET`, `CATALOG_BFF_SHARED_SECRET`, `INTERNAL_API_TOKEN`, `CREDENTIALS_MASTER_KEY`.
  - **dockerHub**: keys `username`, `token`.
- **`PROXY_AUTH_TOKEN` stays unwired** (the R-4 caveat, decided here). Setting it makes `/api/proxy` require `X-Proxy-Auth`, which no client sends, so it would break browsing external catalogs. The ExternalSecret omits it; `secrets.arns.proxy_auth_token` is accepted and ignored (I-159).
- Rendered names in `external-secrets` mode:
  - runtime: `<fullname>-platform`, `<fullname>-db-owner`, and `<fullname>-dockerhub` when the docker key is set;
  - hook copies: `<fullname>-hook-db-master`, `-hook-db-owner`, `-hook-db-stac-api`, `-hook-db-stac-reader`, `-hook-db-proxy-policy`, `-hook-db-cube-server`.
- In `existing` mode both runtime and hook names resolve to the deployment's names.
- Every ExternalSecret renders ESO's defaults explicitly (`target.creationPolicy: Owner`, `target.deletionPolicy: Retain`, and on each `remoteRef` `conversionStrategy: Default`, `decodingStrategy: None`, `metadataPolicy: None`), so Argo CD does not report a permanent diff.
- **DB credentials never enter a URL.** `DATABASE_URL` is `postgresql://<host>:<port>/<name>` with no userinfo; `PGUSER`/`PGPASSWORD` come from `secretKeyRef`. node-postgres (`val()` in `pg/lib/connection-parameters.js`) and libpq both fall back to those variables, so passwords with `@ / % :` are safe.

### Images

- `stac-higher.imageRef(key)`:
  - with a digest: `<global.imageRepository>-<key>@<digest>`;
  - otherwise: `…-<key>:<imageTag or appVersion>`.
- The pulled form passes through `stac-higher.pullRef`. The mirror prefix for a ref's first path segment (its registry host) is chosen as follows: for `ghcr.io`, `global.imageRegistry` if set, else `registry.mirrorPrefixes["ghcr.io"]`; for any other host, `registry.mirrorPrefixes[<host>]`. With no prefix, the ref is left as is. The prefix (trailing `/` trimmed) replaces the host and the digest is unchanged.
- Container `image:` fields use the pulled form. The env refs `PROCESS_RUNTIME_IMAGE`, `PROCESS_RUNTIME_IMAGE_STACTOOLS` and `IMAGE_SCANNER_IMAGE` use the **upstream** form, because image rows keep upstream refs and the executor maps the pull (spec §5).
- Known keys equal `IMAGE_KEYS` in `.github/scripts/release-manifest.mjs`: `app`, `pipeline`, `titiler`, `cube-server`, `image-scanner`, `process-runtime`, `process-runtime-stactools`, `auth-proxy`. Digests are refused in these cases:
  - an unknown key;
  - a value that is not `^sha256:[0-9a-f]{64}$`;
  - a non-empty `imageDigests` that lacks any key the chart references (`app`, `pipeline`, `image-scanner`, `process-runtime`, `process-runtime-stactools`).
- R-6 runs no third-party image. The db Jobs run the pipeline and app images.

### Pod security (verified against the published images on GHCR, 2026-11-03)

| Workload | Image USER | `runAsUser`/`runAsGroup`/`fsGroup` |
|---|---|---|
| app, app-migrate init | `node` = 1000:1000 (`useradd --uid 1000`) | 1000 |
| pipeline, all Python Job containers | `pipeline` = 999:999 (`/etc/passwd` in the image layer) | 999 |

- Every pod: `runAsNonRoot: true`, `seccompProfile: RuntimeDefault`.
- Every container: `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `readOnlyRootFilesystem: true` (the app and pipeline values can turn it off), and an `emptyDir` at `/tmp`. `sizeLimit`s: app 256Mi, pipeline 2Gi, Jobs 64Mi.
- Pipeline and Python Job env add `HOME=/tmp` and `PYTHONDONTWRITEBYTECODE=1`.
- `automountServiceAccountToken`: app `false`, Jobs `false` (default SA), pipeline `true` (K-5 calls the API). Pod Identity and IRSA project their own token volumes regardless.

### Fixed paths and ports

- `/etc/stac-higher/config/hardware-profiles.json` and `/etc/stac-higher/config/image-policy.json` (ConfigMap `<fullname>-config-files`).
- `/etc/stac-higher/db-ca/ca-bundle.pem`.
- `/etc/stac-higher/db/{run_sql.py,bootstrap.sql,pgstac-grants.sql,app-grants.sql}` (hook ConfigMap `<fullname>-db-sql`).
- App: container port `4321` named `http`; Service `<fullname>-app` port 80 → `http`.
- Pipeline: `8083` named `http`; Service `<fullname>-pipeline` port 8083.

### Hooks (Helm + Argo CD + Flux)

- Annotations on every hook object, from `stac-higher.hookAnnotations (dict "weight" N)`:
  `helm.sh/hook: pre-install,pre-upgrade`, `helm.sh/hook-weight: "N"`, `helm.sh/hook-delete-policy: before-hook-creation`, `argocd.argoproj.io/hook: PreSync`, `argocd.argoproj.io/sync-wave: "N"`, `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation`.
- Weights:
  - `-30`: hook ConfigMaps and hook ExternalSecrets;
  - `-20`: `<fullname>-db-bootstrap`;
  - `-15`: `<fullname>-pgstac-migrate`;
  - `-10`: `<fullname>-app-migrate`.
- Hook objects carry label `app.kubernetes.io/component: db-hook`.
- No `hook-succeeded`. Argo's handling of a deleted ExternalSecret between waves is unverified, and a Secret GC'd before the next wave's pod starts would hang the Job. Hook copies therefore persist until the next upgrade or sync (I-156).
- Flux's helm-controller uses the Helm SDK, so the Helm hooks apply as is.
- Templates never use `lookup`, `.Release.IsInstall`/`IsUpgrade`, or anything non-deterministic (`rand*`, `now`, `uuidv4`, `gen*`).

### DB role model (spec §7.2), as the SQL implements it

- **Role names come from each Secret's `username`; passwords from its `password`.** The Terraform `secrets` module sets usernames equal to the role names.
- `db-bootstrap` (as the RDS master):
  - each of the five login roles is created with `LOGIN PASSWORD`, or `ALTER ROLE … WITH LOGIN PASSWORD` on rerun (rotation-safe);
  - the owner also gets `CREATEROLE`;
  - SQL never names `SUPERUSER`, `REPLICATION` or `BYPASSRLS`, which the RDS master cannot set;
  - `GRANT CREATE, CONNECT ON DATABASE current_database() TO owner`;
  - `CREATE EXTENSION IF NOT EXISTS postgis`, `btree_gist`, `unaccent`;
  - `stac_higher_cube_reader NOLOGIN` if missing, then `GRANT stac_higher_cube_reader TO cube`;
  - `ALTER ROLE proxy SET default_transaction_read_only = on`.
- `pgstac-migrate` (as the owner): `pypgstac migrate`, then `GRANT pgstac_ingest TO api` and `GRANT pgstac_read TO reader`.
- `app-migrate` (as the owner): `node app/dist/server/migrate.mjs`, then `GRANT USAGE ON SCHEMA stac_higher TO proxy` and `GRANT SELECT (collection_id, externally_writable, archived) ON stac_higher.collection_settings TO proxy`. These are the exact columns `services/proxy-policy/src/stac_higher_proxy_policy/db.py` reads.
- tipg's vector-table grants for `stac_reader` are R-7's.

### Rules

- **Placeholders only:** `example.com` hosts, `000000000000` accounts, `us-east-1`. Never commit an account id, ARN, hostname, CIDR or secret (ADR 0024).
- Docs carry no status. `docs/FEATURES.md` gets an R-6 row under `### Deployment artifacts (R queue)`. `docs/ISSUES.md` entries go under `## Deployment artifacts (R queue, epic #136)`, numbered **I-156…I-161**. `docs/backend.md` points at the chart README. No ADR.
- **Commits:** `R-6: <what>` with trailer `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Gates:**
  - `helm lint --strict` and `helm template` for every scenario (`scripts/check-helm.sh`);
  - the vitest render suites;
  - `uv run pytest` + `uv run ruff check . ../../infra/helm/stac-higher/files/db` in `services/pipeline/`;
  - `npm run verify`.
- No cluster, no Docker, no AWS. The DB-gated pytest cases run in CI's `pipeline` job, or by the lead with `DATABASE_URL` set.

## Review Focus

1. **A password with URL or SQL metacharacters** (`p'a"ss\w@rd/%`), from an `existing` Secret, must work end to end: no credential ever enters a URL, and the SQL runner quotes it. Tests: Task 4/5 "DATABASE_URL carries no userinfo"; Task 6 `test_bootstrap_handles_metacharacter_passwords`.
2. **`publicUrls.app` with a trailing slash** (`https://app.example.com/`) must yield host `app.example.com` and redirect `https://app.example.com/api/auth/callback`. A path (`https://example.com/stac`) must fail to render, not half-work. Tests: Task 4 `derives the ingress host and redirect from publicUrls.app` and `refuses an app URL with a path`.
3. **A mirror prefix with a trailing slash, or for an upstream the chart does not use** (`docker.io`), must not produce `//` or rewrite the wrong images. Test: Task 4 `rewrites only the mirrored host and trims a trailing slash`.
4. **A deployment-supplied hardware-profile set that can never fit the credential cap** (`max_queue_wait_seconds + grace ≥ aws.processMaxCredentialSeconds`) must fail at render, not at the first deploy's 422. Test: Task 3 `refuses a profile whose queue wait leaves no time under the cap`.
5. **Hooks re-run on every upgrade and every Argo sync, with rotated passwords**: the SQL must be idempotent and must apply the new password. Tests: Task 6 `test_bootstrap_is_idempotent_and_rotates_passwords`, `test_grants_are_idempotent`; Task 7 `renders identically twice`.

---

## File structure

```
infra/helm/stac-higher/
  Chart.yaml  values.yaml  values.schema.json  README.md  .helmignore
  files/hardware-profiles/eks-cpu.json      # the eks-cpu default (Task 3)
  files/image-policy/default.json           # byte copy of infra/image-policy/default.json (Task 3)
  files/db/run_sql.py                       # env → session settings → one transaction (Task 6)
  files/db/bootstrap.sql  pgstac-grants.sql  app-grants.sql   (Task 6)
  templates/_helpers.tpl                    # names, labels, images, secret names, hook annotations, security contexts
  templates/validate.yaml                   # cross-field fail rules; renders nothing
  templates/serviceaccounts.yaml
  templates/externalsecrets.yaml            # runtime ExternalSecrets
  templates/config-files.yaml  config-env.yaml  db-ca.yaml
  templates/app-deployment.yaml  app-service.yaml  app-hpa.yaml  ingress.yaml
  templates/pipeline-deployment.yaml  pipeline-service.yaml  servicemonitor.yaml
  templates/networkpolicies.yaml
  templates/hooks/db-resources.yaml  db-bootstrap-job.yaml  pgstac-migrate-job.yaml  app-migrate-job.yaml
  tests/render.mjs                          # the harness every vitest suite uses
  tests/values/base.yaml  existing-secrets.yaml  irsa.yaml  digests-mirror.yaml  r5-helm-values.yaml  r5-deployment.yaml
infra/examples/aws-eks/values.yaml          # placeholder deployment values (issue scope)
app/src/__tests__/helm/{harness,values,secrets,config,app,pipeline,hooks,network,readme}.test.ts
services/pipeline/tests/test_chart_db_sql.py
scripts/check-helm.sh                       # lint + template every scenario into <out-dir>
.github/workflows/helm.yml
```

**Render-test harness decision.** Use vitest in the app workspace over `helm template`, not the `helm unittest` plugin. The reasons:
- `npm run verify` (the CI `app` job, whose ubuntu runner ships Helm) covers it with no plugin install.
- Tests can import the app's own validators: `parseHardwareProfiles` and `credentialBudgetError` check the `eks-cpu` profile, and the R-3 `IMAGE_KEYS` checks the image keys.
- It follows R-3's pattern (`app/src/__tests__/release-manifest.test.ts` → `.github/scripts/release-manifest.mjs`).
- helm-unittest's YAML assertions cannot express "every object a hook Job reads is a hook at a lower weight".

Locally the suites skip when `helm` is missing. Under `CI=true` a missing `helm` is a hard failure.

---

### Task 1: Chart scaffold, values contract, schema, validation, ServiceAccounts, render harness

**Files:**
- Create: `infra/helm/stac-higher/{Chart.yaml,values.yaml,values.schema.json,.helmignore}`, `templates/_helpers.tpl`, `templates/validate.yaml`, `templates/serviceaccounts.yaml`, `tests/render.mjs`, `tests/values/{base,existing-secrets,irsa,digests-mirror,r5-helm-values,r5-deployment}.yaml`
- Modify: `app/package.json` (devDependency `yaml: ^2.9.1`. It is already in the lockfile tree at 2.9.1, so this makes the import explicit and adds no new package.)
- Test: `app/src/__tests__/helm/harness.test.ts`, `app/src/__tests__/helm/values.test.ts`

**Interfaces:**
- Produces, in `tests/render.mjs` (JSDoc-typed ESM):
  - `CHART_DIR: string`, `VALUES_DIR: string`, `KUBE_VERSION = "1.34.0"`.
  - `helmReady(): boolean`: true when `helm version` runs. It throws `"helm is required when CI is set"` if `process.env.CI` is set and helm is missing.
  - `renderChart({ valuesFiles = [], values = {}, releaseName = "stac-higher", namespace = "stac-higher" }): { ok: boolean, docs: object[], stderr: string }`. It runs `helm template <release> <CHART_DIR> --namespace <ns> --kube-version 1.34.0 -f …`, writing `values` as JSON to a temp file applied last. It parses with `yaml.parseAllDocuments`, dropping empty docs.
  - `scenario(name): string[]`: `[base.yaml]` for `"base"`, otherwise `[base.yaml, <name>.yaml]`.
  - `podSpecs(docs): {kind, name, spec, annotations}[]`: `spec.template.spec` for Deployments and Jobs, plus the template's annotations.
  - `containersOf(spec): object[]`: `initContainers` then `containers`.
  - `effectiveEnv(docs, container): Record<string, string | {secret, key} | {configMap, key}>`. It resolves `envFrom` ConfigMaps from `docs`, then applies explicit `env`, which wins, as in Kubernetes.
- Produces, in `_helpers.tpl`:
  - `stac-higher.fullname` and `stac-higher.labels`/`stac-higher.selectorLabels` (`(dict "root" $ "component" "<c>")`, adding `app.kubernetes.io/component`);
  - `stac-higher.imageKeys` (the eight keys, as a JSON list);
  - `stac-higher.imageRef`/`stac-higher.pullRef`/`stac-higher.image` (`(dict "root" $ "key" "<k>")`, per Global Constraints);
  - `stac-higher.serviceAccountName (dict "root" $ "component" "app"|"pipeline")`, which returns `identity.serviceAccounts.<component>` verbatim (never release-derived);
  - `stac-higher.hookAnnotations (dict "weight" N)`;
  - `stac-higher.podSecurityContext (dict "uid" N)`;
  - `stac-higher.containerSecurityContext (dict "readOnly" bool)`;
  - `stac-higher.appHost` (host of `publicUrls.app`, trailing `/` trimmed).

- [ ] **Step 1: Write the failing tests**

`harness.test.ts` (pure: no helm needed for the helper tests):
- `effectiveEnv resolves envFrom ConfigMaps and lets explicit env win`. Synthetic docs: ConfigMap `c` `{A: "1", B: "2"}`; a container with `envFrom: [{configMapRef: {name: "c"}}]` and `env: [{name: "B", value: "x"}, {name: "S", valueFrom: {secretKeyRef: {name: "s", key: "k"}}}]`. Expect `{A: "1", B: "x", S: {secret: "s", key: "k"}}`.
- `containersOf lists init containers first`.

`values.test.ts` (`describe.skipIf(!helmReady())`):
- `renders the base scenario` and `renders each scenario`: for `base`, `existing-secrets`, `irsa`, `digests-mirror`, `ok === true`, and every doc has `apiVersion`, `kind` and `metadata.name`.
- `refuses a missing required value`: rendering with `values: {publicUrls: {app: null}}` fails, and `stderr` contains `publicUrls`.
- `refuses an unknown secrets mode`: `secrets.mode: "inline"` fails (schema enum).
- `refuses a typo inside the contract`: `publicUrls.catalgo` fails (`additionalProperties`).
- `refuses dev-bypass without the override`: `auth.mode: bypass` fails with stderr containing `auth.allowDevBypass`. With `allowDevBypass: true` it renders.
- `requires the OIDC issuer and client in oidc mode`: blank `auth.issuer` fails, naming `auth.issuer`.
- `requires role ARNs in irsa mode`: `irsa.yaml` with `identity.roleArns.app: ""` fails, naming `identity.roleArns.app`.
- `pod-identity ServiceAccounts carry no role annotation; irsa ones do`. In `base`, SAs `stac-higher-app`/`stac-higher-pipeline` have no `eks.amazonaws.com/role-arn`. In `irsa`, they carry `arn:aws:iam::000000000000:role/stac-higher-example-app` and `…-pipeline`. App SA `automountServiceAccountToken: false`; pipeline SA `true`.
- `ServiceAccounts take R-5's names, not the release name`: render with `releaseName: "other"` and the SAs are still `stac-higher-app`/`stac-higher-pipeline`. With `identity.serviceAccounts.app: "custom-app"`, the app SA is `custom-app`.
- `refuses a namespace the Pod Identity associations do not name`: rendering with `namespace: "elsewhere"` fails, naming `identity.namespace`.
- `accepts R-5's helm_values verbatim`: a fixture `tests/values/r5-helm-values.yaml` holds exactly the R-5 key set: `global.imageRegistry`, `publicUrls.{app,objectStore}`, `aws.*`, `registry.*`, `database.{host,port,name,masterSecretArn,caCertIdentifier}`, `identity.*`, `secrets.{mode,arns}`, `auth.{mode,issuer,clientId,audienceClaim,audiences,claimsMapping,hostedDomain}` and `demoDestination.*`. Layered with `tests/values/r5-deployment.yaml`, which supplies only the non-R-5 keys (`publicUrls.{catalog,titiler,tipg,cubeServer}`, `database.caBundle.existingConfigMap`, `secrets.externalSecrets.storeRef.name`), it renders without `base.yaml`. Optional R-5 sections (`auth`, `demoDestination`) are **omitted** from a second variant, and it still renders with the chart defaults. The same R-5 fixture with any key renamed (`aws.buckett`) fails.
- `the chart's image keys equal the release manifest's`: the rendered/parsed `stac-higher.imageKeys` list equals `IMAGE_KEYS` imported from `../../../../.github/scripts/release-manifest.mjs`. Expose it through a test-only ConfigMap? No. Instead, read `IMAGE_KEYS` and assert each key passes `imageDigests` validation, and that the key `bogus` fails with `global.imageDigests.bogus`.
- `refuses a malformed digest`: `global.imageDigests.app: "sha256:abc"` fails, naming `global.imageDigests.app`.
- `refuses partial digests`: only `app` set → fails, naming the first missing referenced key (`pipeline`).
- `uses neither lookup nor install-only branches`: grep every file under `templates/` for `lookup`, `IsInstall`, `IsUpgrade`, `rand`, `now`, `uuidv4`, `genCA`, `genPrivateKey`, `genSelfSignedCert`, `genSignedCert`; expect none.

- [ ] **Step 2: Run to verify they fail**

Run: `cd app && npx vitest run src/__tests__/helm/` · Expected: FAIL (`render.mjs` / chart missing).

- [ ] **Step 3: Implement**

- `values.yaml` exactly per the contract table, each key commented.
- `values.schema.json` per Global Constraints.
- `validate.yaml` with `{{- include "stac-higher.validate" . -}}`, where the helper `fail`s on:
  - bypass without `allowDevBypass`;
  - oidc without issuer/clientId;
  - irsa without `roleArns.app`/`.pipeline`;
  - the image-digest rules;
  - `publicUrls.app` with a path;
  - `verify-full` without exactly one CA source.

  The secrets-mode and profile rules are added in Tasks 2/3. Messages start with the key path (e.g. `auth.allowDevBypass must be true to render auth.mode=bypass`).
- `serviceaccounts.yaml`: the two SAs, named `identity.serviceAccounts.app` / `.pipeline`. With IRSA, annotation `eks.amazonaws.com/role-arn: identity.roleArns.<c>`. `validate` fails when `.Release.Namespace != identity.namespace`, naming `identity.namespace`.
- Scenario values files:
  - `base.yaml`: external-secrets + pod-identity + oidc + `verify-full` with `existingConfigMap: rds-ca-bundle`. Hosts `*.example.com`. `secrets.arns` = `arn:aws:secretsmanager:us-east-1:000000000000:secret:stac-higher-example/<suffix>` per the secrets README. `database.masterSecretArn` = `…:secret:rds!db-00000000`. `aws.processRunRoleArn: arn:aws:iam::000000000000:role/stac-higher-example-process-run`. `aws.bucket: stac-higher-example-platform`. storeRef `aws-secrets-manager`. The keys are laid out exactly as R-5's `helm_values` emits them.
  - `existing-secrets.yaml`: `mode: existing` with names `stac-higher-platform`, `stac-higher-db-master`, … .
  - `irsa.yaml`: mode irsa with placeholder role ARNs.
  - `digests-mirror.yaml`: all eight digests (`sha256:` + 64 hex per key) and `global.imageRegistry: "000000000000.dkr.ecr.us-east-1.amazonaws.com/ghcr/"`, deliberately with a trailing slash, plus `registry.mirrorPrefixes` with all three hosts.
- `render.mjs` per Interfaces.

- [ ] **Step 4: Run to verify they pass**

Run: `cd app && npx vitest run src/__tests__/helm/` · Expected: PASS.
Run: `helm lint --strict --kube-version 1.34.0 infra/helm/stac-higher -f infra/helm/stac-higher/tests/values/base.yaml` · Expected: `1 chart(s) linted, 0 chart(s) failed`.

- [ ] **Step 5: Commit** · `git add infra/helm app/package.json package-lock.json app/src/__tests__/helm && git commit -m "R-6: chart scaffold, values contract, schema, validation, ServiceAccounts, render harness"`

---

### Task 2: Secrets — runtime ExternalSecrets and the two modes

**Files:**
- Create: `templates/externalsecrets.yaml`
- Modify: `templates/_helpers.tpl`, `templates/validate.yaml`
- Test: `app/src/__tests__/helm/secrets.test.ts`

**Interfaces:**
- Consumes: Task 1 helpers, scenarios.
- Produces:
  - `stac-higher.secretName (dict "root" $ "name" "<logical>")` and `stac-higher.hookSecretName (dict "root" $ "name" "<logical>")`, with logical names `platform`, `dbMaster`, `dbOwner`, `dbStacApi`, `dbStacReader`, `dbProxyPolicy`, `dbCubeServer`, `dockerHub`. Both return the rendered names per Global Constraints. `secretName` of `dockerHub` returns `""` when unset.
  - `stac-higher.externalSecret (dict "root" $ "name" <k8s name> "remoteKey" <key> "json" bool "keys" <list> "hook" bool)`, which renders one ExternalSecret. With `json`, each key maps to `remoteRef.property`.

- [ ] **Step 1: Write the failing tests** (`secrets.test.ts`)
- `renders no Secret in any scenario`: for every scenario, `docs.filter(d => d.kind === "Secret")` is empty.
- `external-secrets mode renders the runtime platform and owner ExternalSecrets`:
  - `apiVersion` is `external-secrets.io/v1`;
  - `secretStoreRef` is `{name: "aws-secrets-manager", kind: "ClusterSecretStore"}`;
  - `<fullname>-platform` has `data[].secretKey` exactly `[SESSION_SECRET, CATALOG_BFF_SHARED_SECRET, INTERNAL_API_TOKEN, CREDENTIALS_MASTER_KEY]`, with `remoteRef.key` equal to the four `secrets.arns` values and no `property`;
  - `<fullname>-db-owner` maps `username`/`password` to `remoteRef.property` `username`/`password` of `secrets.arns.db_stac_higher_owner`;
  - each has `target.creationPolicy: Owner` and `deletionPolicy: Retain`, and each remoteRef carries `conversionStrategy: Default`, `decodingStrategy: None`, `metadataPolicy: None`.
- `never wires PROXY_AUTH_TOKEN`: no rendered string anywhere contains `PROXY_AUTH_TOKEN` or the `secrets.arns.proxy_auth_token` value.
- `existing mode renders no ExternalSecret`.
- `requires every wired remote key`: blank `secrets.arns.session_secret` fails, naming it. Blank `database.masterSecretArn` and blank `storeRef.name` fail too.
- `requires every existing Secret name`: in `existing-secrets` with `secrets.existing.dbCubeServer: ""`, rendering fails, naming `secrets.existing.dbCubeServer`.
- `the Docker Hub credential is optional`: unset → no `<fullname>-dockerhub`. Set `secrets.dockerHubArn` → an ExternalSecret with `username`/`token` from JSON properties.

- [ ] **Step 2: Run** `cd app && npx vitest run src/__tests__/helm/secrets.test.ts` · Expected: FAIL.
- [ ] **Step 3: Implement** the helpers, `externalsecrets.yaml` (runtime: platform, db-owner, optional dockerhub; only in `external-secrets` mode), and the mode rules in `validate`.
- [ ] **Step 4: Run** the same · Expected: PASS.
- [ ] **Step 5: Commit** · `R-6: secrets — runtime ExternalSecrets, existing mode, no literal Secret`

---

### Task 3: Config — the file ConfigMap with the eks-cpu profile, env ConfigMaps, the CA ConfigMap

**Files:**
- Create: `files/hardware-profiles/eks-cpu.json`, `files/image-policy/default.json` (`cp infra/image-policy/default.json`), `templates/config-files.yaml`, `templates/config-env.yaml`, `templates/db-ca.yaml`
- Modify: `templates/_helpers.tpl`, `templates/validate.yaml`
- Test: `app/src/__tests__/helm/config.test.ts`

**Interfaces:**
- Consumes: Task 1/2 helpers.
- Produces:
  - **ConfigMap `<fullname>-config-files`**: keys `hardware-profiles.json` and `image-policy.json`, from values via `toPrettyJson`, or the `files/` copies when `null`.
  - **ConfigMap `<fullname>-env-shared`**:
    - `DATABASE_URL=postgresql://<host>:<port>/<name>`;
    - `PGSSLMODE=verify-full` and `PGSSLROOTCERT=/etc/stac-higher/db-ca/ca-bundle.pem`, only when `verify-full`;
    - `PROCESS_EXECUTOR=kubernetes`;
    - `PROCESS_MAX_CREDENTIAL_SECONDS` from `aws.processMaxCredentialSeconds`, omitted when absent;
    - `PROCESS_CREDENTIAL_GRACE_SECONDS`, `PROCESS_NETWORK_MAX`;
    - `PROCESS_HARDWARE_PROFILES_FILE` and `PROCESS_IMAGE_POLICY_FILE` (the fixed paths);
    - `AWS_REGION` and `AWS_DEFAULT_REGION`.
  - **ConfigMap `<fullname>-env-app`**:
    - `BUILTIN_CATALOG_PUBLIC_URL`, `TITILER_URL`, `TIPG_URL`, `CUBE_SERVER_URL`;
    - `BUILTIN_CATALOG_URL`, only if set;
    - `SAFE_FETCH_ALLOW_HOSTS`: the comma-join of the host of `builtinCatalogInternalUrl` plus `app.safeFetchAllowHosts`, omitted when empty;
    - `S3_ENDPOINT=publicUrls.objectStore`, `S3_BUCKET=aws.bucket`, `S3_REGION=aws.region`, `S3_FORCE_PATH_STYLE`;
    - `AUTH_MODE`, `OIDC_ISSUER=auth.issuer`, `OIDC_ISSUER_INTERNAL` (only if set), `OIDC_CLIENT_ID=auth.clientId`;
    - `OIDC_REDIRECT_URI=<publicUrls.app without trailing />/api/auth/callback`;
    - `AUTH_BEARER_AUDIENCES` (`auth.audiences` comma-joined), `AUTH_BEARER_AUDIENCE_CLAIM=auth.audienceClaim`;
    - `AUTH_CLAIMS_MAPPING` (compact JSON, only if set);
    - in bypass: `AUTH_BYPASS_FORCE=true` and `DEV_AUTH_IDENTITY`, if set;
    - then `app.env`, which wins.
  - **ConfigMap `<fullname>-env-pipeline`**:
    - `STAGING_S3_ENDPOINT` and `PROCESS_RUN_S3_ENDPOINT`, both exactly `objectStore.internalEndpoint`, rendered **even when empty**. The pipeline's unset default is `http://minio:9000` (`config.py` `DEFAULT_STAGING_S3_ENDPOINT`), and `""` → `None` → the AWS default endpoint, which R-5's I-152 needs for STS;
    - `STAGING_BUCKET=aws.bucket`, `STAGING_S3_REGION=aws.region`, `STAGING_S3_FORCE_PATH_STYLE`;
    - `PROCESS_STS_ROLE_ARN=aws.processRunRoleArn`;
    - `PROCESS_RUNTIME_IMAGE`, `PROCESS_RUNTIME_IMAGE_STACTOOLS`, `IMAGE_SCANNER_IMAGE`, all upstream refs from `stac-higher.imageRef`;
    - `EGRESS_ALLOW_HOSTS`, only if non-empty;
    - `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`;
    - then `pipeline.env`.
  - **ConfigMap `<fullname>-db-ca`**: key `ca-bundle.pem`, only when `caBundle.pem` is set.
  - `stac-higher.caVolume (dict "root" $ "hook" bool)` returns the volume (the existing ConfigMap with `items: [{key: <caBundle.key>, path: ca-bundle.pem}]`, or the chart's) or nothing when `disable`. Paired with `stac-higher.caVolumeMount`.
  - `stac-higher.configChecksum` returns the sha256 of the rendered `config-files.yaml` + `config-env.yaml` + `db-ca.yaml`.
- No S3 key and no static AWS key is ever set (spec anti-goal). `DOCKER_HOST`, `PROCESS_NETWORK` and `PROCESS_SCANNER_NETWORK` (Docker-executor only) are not set.

The `eks-cpu.json` document:
- version 1, two profiles. `standard` copies `local.json`'s ranges (cpu 0.25–4 default 1; memory 128–16384 default 512); `cpu-large` copies its ranges too (cpu 2–8 default 4; memory 4096–32768 default 8192).
- Both have `"max_queue_wait_seconds": 900`, `"image": null`, `"gpu_count": null`, `"accelerator": null`.
- Both have `"backend": {"kubernetes": {"queue": "process-runs", "node_selector": {}, "tolerations": [], "runtime_class": null, "extended_resources": {}}}`. This is K spec §3.4's shape; the empty `node_selector` matches the label-less `cpu-default` flavor (K spec §7.1).
- Budget: 900 + GeoColor's 600 + grace 300 = 1800 ≤ 3600, which leaves timeouts up to 2400 s. 900 s covers CPU node provisioning (K spec §7.3: minutes).

- [ ] **Step 1: Write the failing tests** (`config.test.ts`)
- `the default hardware profiles are the eks-cpu set and the app accepts them`: parse `<fullname>-config-files`'s `hardware-profiles.json` with `parseHardwareProfiles` (from `@/lib/processes/hardware`). The ids are `["standard", "cpu-large"]`, and every `backend.kubernetes.queue === "process-runs"`.
- `every eks-cpu profile fits GeoColor under the AWS cap`:
  - read GeoColor's timeout from `services/pipeline/src/pipeline/demo/goes/seed.py` with `/GEOCOLOR_RUNTIME = \{[^}]*"timeout_seconds": (\d+)/s` (currently 600);
  - for each profile, `credentialBudgetError({maxCredentialSeconds: 3600, maxQueueWaitSeconds, timeoutSeconds: geocolor, graceSeconds: 300})` is `null`;
  - the rendered `PROCESS_MAX_CREDENTIAL_SECONDS` is `"3600"`.
- `the chart's image policy is the repo's default`: `files/image-policy/default.json` is byte-equal to `infra/image-policy/default.json`, and the rendered `image-policy.json` parses to the same object.
- `deployment-supplied documents replace the defaults`: with `config.hardwareProfiles` set to a one-profile `standard` doc, the ConfigMap holds it.
- `refuses a profile whose queue wait leaves no time under the cap`: a profile with `max_queue_wait_seconds: 3400` fails (3400 + 300 ≥ 3600), naming `config.hardwareProfiles` and the profile id. With `aws.processMaxCredentialSeconds: null` it renders.
- `shared env carries the executor, the cap, the file paths and a credential-free DATABASE_URL`:
  - `DATABASE_URL` is `postgresql://db.example.com:5432/stac_higher` (base values) and matches no `/\/\/[^/]*@/`;
  - `PROCESS_EXECUTOR` is `kubernetes`;
  - `PGSSLMODE`/`PGSSLROOTCERT` are as pinned.
- `sslMode disable drops the TLS env and the CA volume`.
- `the pipeline's S3 endpoints stay blank on AWS`: under `base`, where `publicUrls.objectStore` is `https://s3.us-east-1.amazonaws.com`, `env-pipeline` has `STAGING_S3_ENDPOINT === ""` and `PROCESS_RUN_S3_ENDPOINT === ""` (present, empty), while the app's `S3_ENDPOINT` is the public URL. With `objectStore.internalEndpoint: "http://minio.example.com:9000"`, both pipeline keys take it.
- `one cap and one grace feed both runtimes`: `PROCESS_MAX_CREDENTIAL_SECONDS` and `PROCESS_CREDENTIAL_GRACE_SECONDS` appear only in `env-shared`, and both Deployments `envFrom` it (asserted in Tasks 4/5 too).
- `app env derives the OIDC redirect and joins audiences`: `publicUrls.app: "https://app.example.com/"` → `OIDC_REDIRECT_URI` is `https://app.example.com/api/auth/callback`. `auth.audiences: ["a","b"]` → `"a,b"`.
- `bypass sets AUTH_BYPASS_FORCE only when allowed`.
- `the internal catalog host joins SAFE_FETCH_ALLOW_HOSTS`: `builtinCatalogInternalUrl: "http://stac-higher-auth-proxy:8080"` plus `safeFetchAllowHosts: ["x.internal"]` → `"stac-higher-auth-proxy,x.internal"`.
- `pipeline env keeps upstream runtime image refs`: under `digests-mirror`, `PROCESS_RUNTIME_IMAGE` is `ghcr.io/techtraverse/stac-higher-process-runtime@sha256:<…>` (starts with `ghcr.io/`, not the mirror).
- `refuses secret-looking extra env`: `app.env: {MY_TOKEN: "x"}` and `pipeline.env: {AWS_SECRET_ACCESS_KEY: "x"}` fail, naming the key. The pattern is `/(SECRET|TOKEN|PASSWORD|_KEY$|_KEY_ID$)/`.
- `no static S3 or AWS key is configured`: no ConfigMap has a key matching `/ACCESS_KEY_ID|SECRET_ACCESS_KEY/`.
- `an inline CA bundle renders the CA ConfigMap`.

- [ ] **Step 2: Run** `cd app && npx vitest run src/__tests__/helm/config.test.ts` · Expected: FAIL.
- [ ] **Step 3: Implement** the files, templates and helpers above, plus the profile and extra-env rules in `validate`.
- [ ] **Step 4: Run** the same · Expected: PASS.
- [ ] **Step 5: Commit** · `R-6: config — eks-cpu profiles, image policy, env ConfigMaps, RDS CA bundle`

---

### Task 4: App — Deployment, Service, HPA, Ingress

**Files:**
- Create: `templates/app-deployment.yaml`, `templates/app-service.yaml`, `templates/app-hpa.yaml`, `templates/ingress.yaml`
- Test: `app/src/__tests__/helm/app.test.ts`

**Interfaces:**
- Consumes: Task 1–3 helpers and ConfigMaps. Secrets via `stac-higher.secretName`.
- Produces:
  - Deployment `<fullname>-app` (component `app`).
  - Service `<fullname>-app`.
  - Ingress `<fullname>-app`.
  - Service `<fullname>-internal-blocked`: port 80, selector `{app.kubernetes.io/name: stac-higher, app.kubernetes.io/component: none}`, which matches no pod.
  - `stac-higher.dbCredentialEnv (dict "secret" <name>)`: env `PGUSER`/`PGPASSWORD` from keys `username`/`password`.

Deployment details:
- `replicas` (omitted when the HPA is on), command from the image (no override), port `http` 4321.
- `envFrom`: `env-shared`, `env-app`.
- `env`:
  - `dbCredentialEnv` of `dbOwner`;
  - `SESSION_SECRET`, `CATALOG_BFF_SHARED_SECRET`, `INTERNAL_API_TOKEN` and `CREDENTIALS_MASTER_KEY` from the `platform` secret.
- Probes: readiness `httpGet /` port http (`initialDelaySeconds: 5`, `periodSeconds: 10`, `timeoutSeconds: 5`); liveness `tcpSocket` http (`periodSeconds: 20`).
- Volumes: config-files at `/etc/stac-higher/config` (read-only), the CA volume, `/tmp` emptyDir 256Mi.
- Pod annotation `checksum/config` from `stac-higher.configChecksum`, merged with `app.podAnnotations`.
- Security per Global Constraints, uid 1000. ServiceAccount `stac-higher.serviceAccountName app`.

HPA: `autoscaling/v2`, CPU utilisation.

Ingress:
- `ingressClassName` (if set); annotations `merge ingress.app.annotations ingress.annotations`.
- One rule, host `stac-higher.appHost`, with paths in this order:
  1. `/api/internal` Prefix → `<fullname>-internal-blocked:80`;
  2. `/` Prefix → `<fullname>-app:http`.
- `tls` when `tlsSecretName` is set.

- [ ] **Step 1: Write the failing tests** (`app.test.ts`)
- `every pulled image is digest-pinned and mirrored`: under `digests-mirror`, every container image in **every** `podSpecs` entry matches `^000000000000\.dkr\.ecr\.us-east-1\.amazonaws\.com/ghcr/techtraverse/stac-higher-[a-z-]+@sha256:[0-9a-f]{64}$`, with no `//`. This test is generic, so it covers the Jobs added in Task 7.
- `rewrites only the mirrored host and trims a trailing slash`: with `registry.mirrorPrefixes: {"docker.io": "m.example.com/hub"}`, no ghcr entry and blank `global.imageRegistry`, images stay `ghcr.io/…`. With only `registry.mirrorPrefixes["ghcr.io"]` set, it is used. With both set, `global.imageRegistry` wins.
- `without digests images use the chart appVersion tag`: `ghcr.io/techtraverse/stac-higher-app:0.1.0`. `global.imageTag: "1.2.3"` gives `:1.2.3`.
- `the app reads DB credentials from the owner Secret, never from a URL`: `effectiveEnv` gives `PGUSER = {secret: "stac-higher-db-owner", key: "username"}`, `PGPASSWORD` the same with `password`, and `DATABASE_URL` without userinfo. In `existing-secrets`, the secret name is `stac-higher-db-owner-existing` (the name in that file).
- `the app gets the four platform secrets and not PROXY_AUTH_TOKEN`.
- `the app pod is hardened`:
  - pod `runAsNonRoot: true`, `runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000`, `seccompProfile.type: RuntimeDefault`;
  - every container `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities.drop: ["ALL"]`;
  - a `/tmp` emptyDir mount;
  - `automountServiceAccountToken: false`;
  - `app.readOnlyRootFilesystem: false` flips only that flag.
- `config changes roll the app`: changing `config.hardwareProfiles` changes the pod-template `checksum/config`; changing `app.replicas` does not.
- `mounts the config files at the paths the env names`: the volume of `<fullname>-config-files` is mounted at `/etc/stac-higher/config`, and the CA at `/etc/stac-higher/db-ca` (base: ConfigMap `rds-ca-bundle`, item `ca-bundle.pem`).
- `the HPA replaces replicas`: `app.autoscaling.enabled: true` → an HPA with min 2 / max 6 / 70 %, and the Deployment has no `replicas`.
- `derives the ingress host and redirect from publicUrls.app`: trailing-slash URL → host `app.example.com`.
- `refuses an app URL with a path`: `https://example.com/stac` fails, naming `publicUrls.app`.
- `the ingress blocks /api/internal ahead of the app`:
  - `paths[0]` is `/api/internal` → `<fullname>-internal-blocked`, and `paths[1]` is `/` → `<fullname>-app`;
  - the blocked Service's selector matches no rendered pod template's labels.
- `ingress class, annotations and TLS pass through`: `className: alb`, `annotations: {"alb.ingress.kubernetes.io/scheme": "internet-facing"}` plus `app.annotations` override, and `tlsSecretName: app-tls` → `spec.tls[0] = {hosts: ["app.example.com"], secretName: "app-tls"}`.

- [ ] **Step 2: Run** `cd app && npx vitest run src/__tests__/helm/app.test.ts` · Expected: FAIL.
- [ ] **Step 3: Implement** the templates per Interfaces.
- [ ] **Step 4: Run** the same · Expected: PASS.
- [ ] **Step 5: Commit** · `R-6: app Deployment, Service, HPA and Ingress (with the /api/internal block)`

---

### Task 5: Pipeline — Deployment, Service, ServiceMonitor

**Files:**
- Create: `templates/pipeline-deployment.yaml`, `templates/pipeline-service.yaml`, `templates/servicemonitor.yaml`
- Test: `app/src/__tests__/helm/pipeline.test.ts`

**Interfaces:**
- Consumes: Tasks 1–4 helpers.
- Produces:
  - Deployment `<fullname>-pipeline` (component `pipeline`).
  - Service `<fullname>-pipeline`: port 8083 named `http`.
  - ServiceMonitor `<fullname>-pipeline` (`monitoring.coreos.com/v1`, endpoint port `http`, path `/metrics`, `interval`, extra `labels`), only when enabled.

Deployment details:
- `replicas: 1`, `strategy: {type: Recreate}` (spec §6.1), `terminationGracePeriodSeconds: 30` (above the worker's 25 s drain, `services/pipeline/README.md` "Shutdown"), command from the image.
- `envFrom`: `env-shared`, `env-pipeline`.
- `env`:
  - `dbCredentialEnv` of `dbOwner`;
  - `CREDENTIALS_MASTER_KEY` from `platform`;
  - `REGISTRY_DOCKERHUB_USER`/`REGISTRY_DOCKERHUB_TOKEN` from `dockerHub` (keys `username`/`token`), only when set.
- Probes: readiness `httpGet /health` port http; liveness `tcpSocket` http. `/health` 503s when the DB is unreachable, so it must not restart the pod.
- Volumes as for the app (`/tmp` 2Gi). Uid 999. `automountServiceAccountToken: true`. Checksum annotation.

- [ ] **Step 1: Write the failing tests** (`pipeline.test.ts`)
- `the pipeline is a single Recreate replica on the Kubernetes executor`: `replicas === 1`, `strategy.type === "Recreate"`, `terminationGracePeriodSeconds >= 30`, effective `PROCESS_EXECUTOR === "kubernetes"`.
- `the pipeline reads DB credentials and the master key from Secrets`: `PGUSER`/`PGPASSWORD` from the owner Secret, `CREDENTIALS_MASTER_KEY` from the platform Secret, no `SESSION_SECRET`, `DATABASE_URL` without userinfo.
- `Docker Hub credentials are wired only when configured`.
- `the pipeline pod is hardened with the image uid`: `runAsUser: 999`, `runAsGroup: 999`, `fsGroup: 999`, the same container rules as the app, effective `HOME === "/tmp"`, and `automountServiceAccountToken: true`.
- `probes: ready on /health, alive on the port`.
- `the ServiceMonitor is opt-in`: none by default. When enabled, it selects the pipeline Service's labels on port `http`, path `/metrics`.
- `both Deployments mount the same config ConfigMap and carry the same checksum`.

- [ ] **Step 2: Run** `cd app && npx vitest run src/__tests__/helm/pipeline.test.ts` · Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the same · Expected: PASS.
- [ ] **Step 5: Commit** · `R-6: pipeline Deployment (1 replica, Recreate), Service, optional ServiceMonitor`

---

### Task 6: DB role SQL and its runner (pytest)

**Files:**
- Create: `infra/helm/stac-higher/files/db/run_sql.py`, `files/db/bootstrap.sql`, `files/db/pgstac-grants.sql`, `files/db/app-grants.sql`
- Create: `services/pipeline/tests/test_chart_db_sql.py`
- Modify: `.github/workflows/ci.yml` (pipeline job's ruff line adds `../../infra/helm/stac-higher/files/db`)

**Interfaces:**
- Produces, in `run_sql.py` (stdlib + psycopg only, Python 3.12):
  - `ENV_PREFIX = "STAC_HIGHER_SQL_"`, `GUC_PREFIX = "stac_higher_sql."`.
  - `settings_from_env(env: Mapping[str, str]) -> dict[str, str]`: `STAC_HIGHER_SQL_OWNER_ROLE` → `stac_higher_sql.owner_role` (suffix lowercased); other variables are ignored.
  - `apply(conn: psycopg.Connection, sql: str, settings: dict[str, str]) -> None`: one transaction. It runs `SELECT set_config(%s, %s, true)` for each setting (parameters, never interpolated), then `conn.execute(sql)` as one multi-statement simple query, then commits.
  - `main(argv: list[str] | None = None, env: Mapping[str, str] = os.environ, connect: Callable[[], psycopg.Connection] = lambda: psycopg.connect("")) -> int`. `argv[0]` is the SQL path. `connect("")` uses libpq's `PG*` env, so TLS comes from `PGSSLMODE`/`PGSSLROOTCERT`. It prints `applied <basename> to <host>:<port>/<db> as <user>` and returns 0. On `psycopg.Error` it prints `<basename> failed: <sqlstate> <diag.message_primary>` and returns 1. It never prints a setting value. `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`.
- SQL contract: pure SQL, idempotent, reading only `current_setting('stac_higher_sql.<name>')`. Statements are built with `format('%I' / '%L')` inside `DO` blocks, so passwords never appear in a top-level logged statement.
  - `bootstrap.sql` reads `owner_role`, `owner_password`, `api_role`, `api_password`, `reader_role`, `reader_password`, `proxy_role`, `proxy_password`, `cube_role`, `cube_password`, and does exactly the bootstrap list in Global Constraints.
  - `pgstac-grants.sql` reads `api_role`, `reader_role`.
  - `app-grants.sql` reads `proxy_role`.

- [ ] **Step 1: Write the failing tests** (`test_chart_db_sql.py`)

Unit tests, always run:
- `test_settings_from_env_maps_and_lowercases`: `{"STAC_HIGHER_SQL_OWNER_ROLE": "o", "PATH": "x"}` → `{"stac_higher_sql.owner_role": "o"}`.
- `test_main_reports_failure_without_values`: a fake `connect` whose `execute` raises a `psycopg.errors.InsufficientPrivilege` returns 1. Captured stdout has the basename and does not contain a setting value (`"s3cr3t"`).

DB-gated tests (`pytestmark` skip unless `DATABASE_URL`, the repo pattern):
- A fixture creates a throwaway database `chart_sql_<hex>` and role names prefixed `t<hex>_`. Teardown runs `DROP DATABASE … WITH (FORCE)`, then `DROP ROLE IF EXISTS` for each test role.
- `run_sql` is loaded with `importlib.util.spec_from_file_location` from `REPO/infra/helm/stac-higher/files/db/run_sql.py` (`REPO = Path(__file__).resolve().parents[3]`).
- `test_bootstrap_creates_login_roles_and_extensions`:
  - `pg_roles` shows the five roles with `rolcanlogin`;
  - the owner has `rolcreaterole` and not `rolsuper`;
  - the others have no `rolcreaterole`;
  - `pg_extension` holds `postgis`, `btree_gist`, `unaccent`;
  - `has_database_privilege(owner, db, 'CREATE')`;
  - `cube_role` is a member of `stac_higher_cube_reader`;
  - `proxy_role`'s `pg_db_role_setting` has `default_transaction_read_only=on`.
- `test_owner_can_create_schemas`: connecting as the owner with its password, `CREATE SCHEMA pgstac_probe` succeeds.
- `test_bootstrap_is_idempotent_and_rotates_passwords`: a second run with new passwords succeeds. The owner logs in with the new password and fails with the old one.
- `test_bootstrap_handles_metacharacter_passwords`: password `p'a"ss\w@rd/%` → login succeeds.
- `test_grants_are_idempotent`: skips unless `pgstac_ingest`/`pgstac_read` and `stac_higher.collection_settings` exist (the migrated CI DB). In that database, create the prefixed api/reader/proxy roles and run `pgstac-grants.sql` and `app-grants.sql` twice. Then:
  - memberships `pgstac_ingest` and `pgstac_read` are present;
  - as the proxy role, `SELECT collection_id, externally_writable, archived FROM stac_higher.collection_settings` works;
  - `SELECT * …` raises `InsufficientPrivilege`;
  - a write raises `ReadOnlySqlTransaction`.

  Teardown revokes and drops the roles.
- `test_bootstrap_requires_every_setting`: running bootstrap with `cube_password` missing returns 1 (`unrecognized configuration parameter`).

- [ ] **Step 2: Run** `cd services/pipeline && uv run pytest tests/test_chart_db_sql.py -q` · Expected: FAIL on the unit tests (`run_sql.py` missing). The DB tests SKIP without `DATABASE_URL`.
- [ ] **Step 3: Implement** the runner and the three SQL files per Interfaces.
- [ ] **Step 4: Run**
  - `cd services/pipeline && uv run pytest -q && uv run ruff check . ../process-runtime/stac_higher_stactools ../image-scanner/stac_higher_scanner ../../infra/helm/stac-higher/files/db` · Expected: PASS, with DB tests skipped locally.
  - **Lead or CI:** `DATABASE_URL=postgresql://username:password@localhost:5433/postgis uv run pytest tests/test_chart_db_sql.py -q` · Expected: all pass. The CI `pipeline` job runs them against the migrated pgstac service.
- [ ] **Step 5: Commit** · `R-6: db role SQL (bootstrap, pgstac grants, app grants) and its psycopg runner`

---

### Task 7: Hook Jobs — db-bootstrap, pgstac-migrate, app-migrate, and their hook resources

**Files:**
- Create: `templates/hooks/db-resources.yaml`, `templates/hooks/db-bootstrap-job.yaml`, `templates/hooks/pgstac-migrate-job.yaml`, `templates/hooks/app-migrate-job.yaml`
- Test: `app/src/__tests__/helm/hooks.test.ts`

**Interfaces:**
- Consumes:
  - `stac-higher.hookAnnotations`, `hookSecretName`, `externalSecret` (with `"hook" true`), `caVolume (dict … "hook" true)`;
  - `files/db/*` via `.Files.Get`.
- Produces:
  - **Hook resources, weight -30:**
    - ConfigMap `<fullname>-db-sql` (`run_sql.py` + the three SQL files);
    - ConfigMap `<fullname>-hook-db-ca`, only when `caBundle.pem`;
    - in `external-secrets` mode, the six hook ExternalSecrets (`-hook-db-master` from `database.masterSecretArn`; the five roles from their `secrets.arns`; JSON `username`/`password`).
  - **Job pod common:**
    - `restartPolicy: Never`, `backoffLimit`/`activeDeadlineSeconds` from `dbJobs`;
    - default ServiceAccount, `automountServiceAccountToken: false`;
    - uid 999 at the pod level, with the app-migrate init container overriding to `runAsUser: 1000`/`runAsGroup: 1000`;
    - the hardened container context, `/tmp` emptyDir 64Mi;
    - `<fullname>-db-sql` at `/etc/stac-higher/db`, the hook CA volume;
    - `stac-higher.dbJobEnv (dict "root" $ "secret" <hook secret>)` = `PGHOST`, `PGPORT`, `PGDATABASE`, `PGSSLMODE`/`PGSSLROOTCERT` (when `verify-full`), `PGUSER`/`PGPASSWORD` from the secret, `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`;
    - `dbJobs.resources`/`nodeSelector`/`tolerations`/`affinity`.
  - **`<fullname>-db-bootstrap`** (weight -20): one container, pipeline image, `["python", "/etc/stac-higher/db/run_sql.py", "/etc/stac-higher/db/bootstrap.sql"]`, `dbJobEnv` of `dbMaster`, plus `STAC_HIGHER_SQL_{OWNER,API,READER,PROXY,CUBE}_{ROLE,PASSWORD}` from the `username`/`password` keys of the five hook role secrets.
  - **`<fullname>-pgstac-migrate`** (weight -15):
    - init container `pypgstac-migrate`: pipeline image, `["pypgstac", "migrate"]`, `dbJobEnv` of `dbOwner`;
    - container `pgstac-grants`: `run_sql.py …/pgstac-grants.sql`, plus `STAC_HIGHER_SQL_API_ROLE`/`READER_ROLE` from the `username` keys.
  - **`<fullname>-app-migrate`** (weight -10):
    - init container `app-migrate`: app image, `["node", "app/dist/server/migrate.mjs"]`, `dbJobEnv` of `dbOwner` plus `DATABASE_URL` (credential-free; node-postgres reads `PGUSER`/`PGPASSWORD`);
    - container `app-grants`: pipeline image, `run_sql.py …/app-grants.sql`, `STAC_HIGHER_SQL_PROXY_ROLE` from the `dbProxyPolicy` username.

- [ ] **Step 1: Write the failing tests** (`hooks.test.ts`)
- `the three DB Jobs are ordered pre-install,pre-upgrade hooks under Helm and Argo`:
  - names and weights `db-bootstrap -20`, `pgstac-migrate -15`, `app-migrate -10`;
  - each has `helm.sh/hook === "pre-install,pre-upgrade"`, `argocd.argoproj.io/hook === "PreSync"`, `sync-wave === hook-weight`, and both delete policies `before-hook-creation` / `BeforeHookCreation`.
- `every object a hook Job reads is an earlier hook or deployment-supplied`. For each Job, collect:
  - volume `configMap.name`;
  - every `secretKeyRef.name`/`configMapKeyRef.name`;
  - every `envFrom` ref.

  Each name must either resolve to a rendered ConfigMap, or to an ExternalSecret whose `spec.target.name` is the name. That object must carry `helm.sh/hook` and `argocd.argoproj.io/hook` with weight < the Job's. Otherwise the name must be one of the deployment-supplied names (the `secrets.existing.*` values, `database.caBundle.existingConfigMap`). Run this under `base`, `existing-secrets`, and base + inline `pem`.
- `Deployments reference no hook object`.
- `only db-bootstrap reads the master credential`: across all `podSpecs`, references to `hookSecretName dbMaster` appear only in `<fullname>-db-bootstrap`, and no Deployment references it.
- `db-bootstrap maps every role's username and password into settings`: the ten `STAC_HIGHER_SQL_*` env entries resolve to `{secret: <hook role secret>, key: "username"|"password"}`.
- `pgstac-migrate runs pypgstac migrate before the grants, as the owner`: `initContainers[0].command` is `["pypgstac", "migrate"]`, and `PGUSER` is from the hook owner secret.
- `app-migrate runs the migrate CLI from the app image before the app grants`: init `command` is `["node", "app/dist/server/migrate.mjs"]` with the app image; `DATABASE_URL` has no userinfo; the grants container uses the pipeline image.
- `hook Jobs carry no ServiceAccount token and run hardened` (uids 999 and 1000 as pinned).
- `existing mode renders the Jobs against the deployment's Secrets and no ExternalSecret`.
- `renders identically twice`: two renders of `base` give equal YAML text.
- `the db-sql ConfigMap carries the chart's SQL files verbatim`: its data equals the four `files/db/*` contents.

- [ ] **Step 2: Run** `cd app && npx vitest run src/__tests__/helm/hooks.test.ts` · Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `cd app && npx vitest run src/__tests__/helm/` · Expected: all helm suites PASS, including Task 4's generic digest/mirror test now covering the Jobs.
- [ ] **Step 5: Commit** · `R-6: db-bootstrap, pgstac-migrate and app-migrate hook Jobs (Helm + Argo CD) with hook-scoped inputs`

---

### Task 8: NetworkPolicies for the `stac-higher` namespace

**Files:**
- Create: `templates/networkpolicies.yaml`
- Test: `app/src/__tests__/helm/network.test.ts`

**Interfaces:**
- Produces, only when `networkPolicy.enabled`:
  - **`<fullname>-default-deny-ingress`**: `podSelector: {}`, `policyTypes: [Ingress]`, no `ingress`. This denies all ingress to every pod in the release namespace, so the README says to install into a dedicated namespace.
  - **`<fullname>-app`**: podSelector app component, ingress on TCP 4321. With `appIngressFrom: null`, the rule has no `from` (any source). With a list, `from` is that list.
  - **`<fullname>-pipeline-metrics`**: only when `metricsFrom` is non-empty; podSelector pipeline component, TCP 8083 from `metricsFrom`.
- There are no egress policies: egress stays open (spec §6.5, #68). Kubelet probes are unaffected (NetworkPolicy always allows node-to-pod traffic). R-7 adds the service-to-service rules for the catalog plane.

- [ ] **Step 1: Write the failing tests** (`network.test.ts`)
- `default-deny ingress covers the namespace`.
- `the app port is open by default and restrictable`: by default, the single ingress rule has `ports: [{protocol: "TCP", port: 4321}]` and no `from`. With `appIngressFrom: [{ipBlock: {cidr: "10.0.0.0/16"}}]` (a test-only literal), `from` equals it.
- `the pipeline accepts no ingress unless metrics scraping is allowed`: by default, no policy selects the pipeline component except default-deny. With `metricsFrom: [{namespaceSelector: {matchLabels: {"kubernetes.io/metadata.name": "monitoring"}}}]`, a policy allows 8083 from it.
- `policies select the workloads' real labels`: each policy's `podSelector.matchLabels` is a subset of the targeted Deployment's pod-template labels.
- `networkPolicy.enabled false renders none`.

- [ ] **Step 2: Run** `cd app && npx vitest run src/__tests__/helm/network.test.ts` · Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the same · Expected: PASS.
- [ ] **Step 5: Commit** · `R-6: stac-higher namespace NetworkPolicies (default-deny ingress, app, pipeline metrics)`

---

### Task 9: CI — `scripts/check-helm.sh`, `.github/workflows/helm.yml`, the aws-eks example values

**Files:**
- Create: `scripts/check-helm.sh`, `.github/workflows/helm.yml`, `infra/examples/aws-eks/values.yaml`
- Modify: `infra/examples/aws-eks/README.md` (one paragraph on `values.yaml`: placeholders; R-5 adds the `helm_values` output it will come from)

**Interfaces:**
- `scripts/check-helm.sh <out-dir>` (bash, `set -euo pipefail`, run from the repo root). Scenarios:
  - `base`: `[base.yaml]`;
  - `existing-secrets`, `irsa`, `digests-mirror`: base + each;
  - `existing-irsa`: base + `existing-secrets.yaml` + `irsa.yaml`;
  - `r5`: `r5-helm-values.yaml` + `r5-deployment.yaml` (no base);
  - `example`: `infra/examples/aws-eks/values.yaml` alone.

  Seven scenarios in all. For each, it runs `helm lint --strict --kube-version 1.34.0 infra/helm/stac-higher <-f …>`, then `helm template stac-higher infra/helm/stac-higher --namespace stac-higher --kube-version 1.34.0 <-f …> > <out-dir>/<scenario>.yaml`. It exits non-zero on the first failure, naming the scenario.
- `infra/examples/aws-eks/values.yaml`: a complete, `external-secrets` + `pod-identity` deployment values file using only placeholders. Its R-5 half uses exactly the `r5-helm-values.yaml` layout under a comment `# from tofu output -raw helm_values (R-5)`. The rest is marked deployment-supplied.
- `helm.yml`:
  - `name: Helm`, `permissions: contents: read`, concurrency like `infra.yml`.
  - Path-filtered on push to `main` and on PRs: `infra/helm/**`, `infra/examples/aws-eks/values.yaml`, `infra/image-policy/**`, `scripts/check-helm.sh`, `app/src/__tests__/helm/**`, `.github/workflows/helm.yml`; plus `workflow_dispatch`.
  - **Job `render` (matrix `helm: [v3.22.1, v4.3.1]`)**: `actions/checkout@v7`, `azure/setup-helm@v5.0.1` (`version: ${{ matrix.helm }}`), `actions/setup-node@v7` (22, npm cache), `npm ci`, `scripts/check-helm.sh "$RUNNER_TEMP/rendered"`, then `npx vitest run src/__tests__/helm/` in `app/`.
  - **Job `validate` (Helm v4.3.1)**:
    - `scripts/check-helm.sh rendered`;
    - install kubeconform v0.8.0 from the release tarball `kubeconform-linux-amd64.tar.gz`, verifying sha256 `9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883`;
    - `kubeconform -strict -summary -kubernetes-version 1.34.0 -schema-location default -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/6ab3493622cd387923a2d0da5234f53e80e1bd28/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' rendered/*.yaml`. The CRDs used are `external-secrets.io/ExternalSecret` v1 and `monitoring.coreos.com/ServiceMonitor` v1; enable the ServiceMonitor in one scenario by adding `pipeline.metrics.serviceMonitor.enabled: true` to `digests-mirror.yaml`;
    - `aquasecurity/trivy-action@v0.36.0` with `scan-type: config`, `scan-ref: rendered`, `severity: HIGH,CRITICAL`, `exit-code: "1"`.
  - The rendered output is scanned, not the chart directory, because trivy silently skips a chart whose schema rejects its defaults (verified locally with trivy 0.69.3: "Supported files … not found", exit 0). `security.yml` is unchanged.

- [ ] **Step 1: Write `scripts/check-helm.sh` and the example values; run it**
Run: `scripts/check-helm.sh "$TMPDIR/r6-rendered"` · Expected: seven `1 chart(s) linted, 0 chart(s) failed` lines and seven rendered files.
- [ ] **Step 2: Add the example to the render suite** in `values.test.ts`: `renders the aws-eks example values alone`, with `ok` true and no Secret.
Run: `cd app && npx vitest run src/__tests__/helm/values.test.ts` · Expected: PASS.
- [ ] **Step 3: Scan the rendered output locally**
Run: `trivy config --severity HIGH,CRITICAL --exit-code 1 --skip-version-check "$TMPDIR/r6-rendered"` · Expected: exit 0, no HIGH/CRITICAL findings. A finding is fixed in the template, not suppressed.
- [ ] **Step 4: Write `.github/workflows/helm.yml`** per Interfaces. Check it parses: `node -e "require('yaml').parse(require('fs').readFileSync('.github/workflows/helm.yml','utf8'))"` · Expected: no output.
- [ ] **Step 5: Commit** · `R-6: helm CI (lint, template, render tests on Helm 3 and 4, kubeconform, trivy) and aws-eks example values`

---

### Task 10: Chart README, docs, final gates

**Files:**
- Create: `infra/helm/stac-higher/README.md`, `app/src/__tests__/helm/readme.test.ts`
- Modify:
  - `docs/FEATURES.md`: an R-6 row under `### Deployment artifacts (R queue)`.
  - `docs/ISSUES.md`: I-156…I-161 under `## Deployment artifacts (R queue, epic #136)`.
  - `docs/backend.md`: the "Where each area is documented" row "Deploying to AWS (Terraform/OpenTofu modules; the Helm chart follows in R-6)" becomes "Deploying to AWS (Terraform/OpenTofu modules, Helm chart)", linking `../infra/helm/stac-higher/README.md` too.
  - `infra/terraform/README.md`: one line pointing at the chart README for the Kubernetes side.
  - `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md`: §6's opening `charts/stac-higher/` and §10's `charts/` become `infra/helm/stac-higher/` (path correction only).

**Interfaces:** none new.

The README contains, in this order:
1. A header with `Tracking: epic #136` and the spec link.
2. **Cluster prerequisites**, from spec §4.1 verbatim in substance:
   - EKS 1.34+ (Auto Mode);
   - NetworkPolicy enforcement;
   - Kueue v0.19+ with `waitForPodsReady` on and `managedJobsWithoutQueueName: false`;
   - ESO with a `ClusterSecretStore`, or pre-created Secrets;
   - an IngressClass and TLS;
   - optional Prometheus Operator;
   - plus: a dedicated namespace (default-deny), and the RDS CA bundle ConfigMap when not passed inline.
3. **Values**: one table row per key path at depth ≤ 2 (the contract table, condensed), with default and meaning.
4. **Secrets modes**: the `external-secrets` mapping from the ten Terraform keys and the master key; the `existing` Secret layouts; and a dev recipe (`kubectl create secret generic … --from-literal=username=… --from-literal=password=…` per db role, and one for `platform`) that writes nothing into values.
5. **Database jobs**: the three hooks, their weights, which credential each reads, and that they re-run on every upgrade/sync (idempotent).
6. **Install paths**:
   - `helm upgrade --install stac-higher oci://ghcr.io/<owner>/charts/stac-higher --version <v> -n stac-higher --create-namespace -f values.yaml -f digests.values.yaml --timeout 15m`, with the `jq` digests recipe from `docs/releases.md`;
   - an Argo CD `Application` sketch (Helm source, `helm.valueFiles`, a note that `PreSync` hooks map from the chart's annotations);
   - a Flux `HelmRelease` sketch (`chartRef` OCIRepository, `timeout: 15m`).
7. **Images**: digests, the mirror rewrite, and the upstream refs kept for runtime images.
8. **Uninstall**: hook objects (label `app.kubernetes.io/component=db-hook`) are left behind, with the `kubectl delete` line.
9. **What later slices add** (R-7, R-8, R-10), one line each.

ISSUES entries. Each is a short paragraph (problem, consequence, mitigation), with a `Tracked in:` line only where actionable:
- **I-156 · Hook-scoped credential Secrets, including the RDS master's, stay in the namespace until the next upgrade or sync 🟡**. `before-hook-creation` only, because Argo's deletion timing for `HookSucceeded` on a Secret-bearing hook is unverified. Follow-up idea: the bootstrap reads the master from Secrets Manager with its own workload identity.
- **I-157 · The role chain is proven on a superuser Postgres only 🟡**. The pytest bootstrap runs as a superuser. Three things are first exercised on RDS (master ≠ superuser): pypgstac migrating as the non-superuser owner, the owner granting `pgstac_*` roles it created (PG16 `CREATEROLE` rules), and the search_path. Tracked in: GitHub #147.
- **I-158 · `/api/internal` is blocked at the Ingress by rule order 🟡**. The block depends on the controller evaluating `/api/internal` before `/` (listed first; longest-prefix on nginx). The route also fails closed without `INTERNAL_API_TOKEN`.
- **I-159 · `PROXY_AUTH_TOKEN` is generated but not wired 🟡**. No client sends `X-Proxy-Auth`.
- **I-160 · The pipeline runs the Docker executor until K-5 🟡**. `PROCESS_EXECUTOR=kubernetes` is set and unread today. In a cluster, process runs fail at launch (no socket proxy). K-5 adds its own settings (runs namespace, etc.) to the chart. Tracked in: GitHub #13.
- **I-161 · Read-only root filesystems and the open app port are render-verified defaults 🟡**. Escape hatches: `app.readOnlyRootFilesystem`, `pipeline.readOnlyRootFilesystem`, and `networkPolicy.appIngressFrom`, which should be set to the VPC CIDR for ALB IP targets.

The FEATURES row reads: `| R-6 · Helm chart skeleton | ✅ | \`infra/helm/stac-higher/\` (README): app (Deployment/Service/HPA/Ingress, \`/api/internal\` blocked), pipeline (1 replica, Recreate, \`PROCESS_EXECUTOR=kubernetes\`), db-bootstrap → pgstac-migrate → app-migrate hook Jobs (Helm + Argo CD) creating the §7.2 roles, eks-cpu hardware profiles + image policy ConfigMap into both Deployments, ExternalSecrets or existing Secrets (never literal), stac-higher NetworkPolicies; render tests (vitest over \`helm template\`), pytest for the role SQL, \`helm.yml\` (lint on Helm 3 and 4, kubeconform, trivy) |`.

- [ ] **Step 1: Write the failing test** (`readme.test.ts`): `the chart README documents every values key to two levels`. Flatten `values.yaml` (parsed with `yaml`) to key paths of depth 1 and 2, e.g. `global.imageDigests`, `secrets.externalSecrets`, `app.resources`. Each must appear in the README as `` `path` ``.
Run: `cd app && npx vitest run src/__tests__/helm/readme.test.ts` · Expected: FAIL (README missing).
- [ ] **Step 2: Write the README and the doc edits** above.
- [ ] **Step 3: Run the gates**
  - `cd app && npx vitest run src/__tests__/helm/` · Expected: PASS.
  - `scripts/check-helm.sh "$TMPDIR/r6-rendered"` · Expected: seven lints pass.
  - `cd services/pipeline && uv run pytest -q && uv run ruff check . ../process-runtime/stac_higher_stactools ../image-scanner/stac_higher_scanner ../../infra/helm/stac-higher/files/db` · Expected: PASS.
  - `npm run verify` (repo root) · Expected: check, build and all unit tests green.
  - `git grep -nE '[0-9]{12}' -- infra/helm infra/examples/aws-eks/values.yaml | grep -v 000000000000` · Expected: no output (no real account id).
- [ ] **Step 4: Commit** · `R-6: chart README, FEATURES row, ISSUES I-156..I-161, backend.md pointer, spec path correction`

**PR (lead):**
- The body starts `Closes #142`.
- It lists the gates run.
- Lead-only steps: none (render-only; the kind install smoke is a later slice). The DB-gated pytest runs in CI.
- Deviations to state:
  - `infra/helm/` instead of `charts/`;
  - no `inline` secrets mode;
  - SQL run through the pipeline image's Python rather than psql;
  - master-created `stac_higher_cube_reader`;
  - `PROXY_AUTH_TOKEN` unwired;
  - hook copies kept until the next sync.
- Hand R-5 the gaps: `publicUrls.{catalog,titiler,tipg,cubeServer}` and a CA bundle source are deployment-supplied unless R-5 adds them.
