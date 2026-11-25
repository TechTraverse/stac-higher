# R-7 · Chart: catalog plane (stac-api, auth-proxy, titiler, tipg) + cube-server — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Tracking:** GitHub #143 (epic #136).

**Goal:** Add the catalog plane and the cube-server to the `stac-higher` chart (`infra/helm/stac-higher/`): stac-fastapi-pgstac, the derived auth-proxy (ADR 0015, enforced mode), our derived titiler, tipg and the cube-server, each with its own database login (spec §7.2), runtime Secrets, Service, Ingress host and NetworkPolicy, proven by the R-6 render suites, lint, kubeconform and trivy.

**Architecture:** The eoapi-k8s fit check (below) failed, so the spec §6.1 fallback applies: the four catalog services are our own templates, rendered by one shared Deployment helper, next to the cube-server. No chart dependency is added (no `Chart.lock`, no vendored archive). Each workload gets a dedicated env ConfigMap with no credential in it; DB logins come from **runtime** Secrets (`<fullname>-db-stac-api`, `-db-stac-reader`, `-db-proxy-policy`, `-db-cube-server`, new ExternalSecrets in `external-secrets` mode), never from the hook copies (I-156). The app's `BUILTIN_CATALOG_URL` defaults to the in-cluster auth-proxy Service.

**Tech Stack:** Helm 3.22 / 4.3, chart `apiVersion: v2`; vitest render suites over `helm template` (R-6 harness); kubeconform 0.8.0; trivy.

**Spec:** `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md` §3, §5, §6.1, §6.4, §6.5, §7.2; ADR 0015 (proxy write policy, stac-fastapi/auth-proxy pin pair), ADR 0024; R-6 plan `2026-11-03-r6-helm-chart.md` (all its Global Constraints still bind); `docs/backend.md` "Deployment: S3 credentials and database TLS (R-2)"; `infra/compose.auth-enforced.yml` (the enforced proxy env this chart mirrors); `services/proxy-policy/README.md`; `infra/cube-server/README.md`; `infra/titiler/README.md`.

## Fit check (spec §6.1) — result: fallback taken

Done 2026-11-24 with Helm 4.1: `helm pull eoapi --repo https://devseed.com/eoapi-k8s/` at 0.17.4 (the spec's version) and 0.17.6 (latest 0.17.x; the templates that matter are identical), then a scratch copy of this chart with `eoapi` as a dependency (`helm dependency build`, `helm template` with `postgresql.type: external-secret`, `postgrescluster.enabled: false`, `pgstacBootstrap.enabled: false`, `ingress/browser/docServer/multidim` off, our titiler image by digest with an amd64 node affinity, the digest-pinned stac-fastapi 6.3.1, and `stac-auth-proxy.enabled` with our derived image). It **renders**, and two of the four requirements hold (pgstac bootstrap off; Crunchy PGO off). Four things fight the spec and cannot be fixed by values:

1. **One database login for every service.** `postgresql.external.existingSecret` is a single Secret that `eoapi.postgresqlEnv` writes as explicit `env` (`PGUSER`, `PGPASSWORD`, `POSTGRES_USER`, `POSTGRES_PASS`) into stac, raster and vector alike. The per-service `settings.envSecrets` is `envFrom`, which explicit `env` overrides, so stac-fastapi cannot be `stac_api` while titiler and tipg are `stac_reader` (§7.2). It also renders `DATABASE_URL=postgresql://$(PGUSER):$(PGPASSWORD)@…`, a credential in a URL, which R-6 forbids.
2. **The stac-auth-proxy subchart (1.3.1) renders `env` as literal `value:` only** — no `valueFrom`, no `envFrom`. The enforced proxy's `CATALOG_BFF_SHARED_SECRET` and its policy role's password would have to be literal values, which §6.4 forbids ("the chart never renders a secret value from values"). Its image is `repository:tag` only (no digest field).
3. **Images cannot follow `global.imageDigests` or the mirror rewrite.** Helm does not template values, so the parent cannot hand its computed refs (digest from the release manifest, ECR pull-through prefix) to the subchart; a deployment would hand-copy four image refs, against spec §11 ("the chart's `global.imageDigests` takes it directly").
4. **One ServiceAccount for stac, raster and vector** (`serviceAccount.name`), so the titiler S3 identity (R-5 `identity.roleArns.titiler`) would also reach stac-fastapi and tipg. Lesser: raster has no `nodeSelector` (affinity works), and the default pod/container security contexts are empty.

So the four catalog services are our own templates; nothing else in the spec changes (§6.1). No ADR: the spec pre-authorised this exit, it adds no dependency and stays reversible.

## Global Constraints

R-6's constraints hold unchanged (placeholders only, no `lookup`/non-determinism, never a `kind: Secret`, no credential in a URL, hardened pods, `/tmp` emptyDir, schema + `validate` messages that start with the key path). Additions:

### Workloads (verified against the published images, 2026-11-24, by reading `/etc/passwd` and the image config from GHCR)

| Component (label) | Image | Port | uid:gid | DB login (runtime Secret) | ServiceAccount | Command |
|---|---|---|---|---|---|---|
| `stac-api` | `catalog.stacApi.image`, default `ghcr.io/stac-utils/stac-fastapi-pgstac:6.3.1@sha256:db117b…` (= `docker-compose.yml` `api`) | 8082 | `user` 1000:1000 | `dbStacApi` → `PGUSER`/`PGPASSWORD` | `default`, no token | `uvicorn stac_fastapi.pgstac.app:app --host 0.0.0.0 --port 8082 --proxy-headers --forwarded-allow-ips=*` |
| `auth-proxy` | chart key `auth-proxy` (derived image, ADR 0015) | 8000 | `user` 1001:1001 | `dbProxyPolicy` → `PGUSER`/`PGPASSWORD` (libpq fallback for `POLICY_DATABASE_URL`) | `default`, no token | image CMD (`python -m stac_higher_proxy_policy`) |
| `titiler` | chart key `titiler` | 8084 | `titiler` 1001:999 | `dbStacReader` → `POSTGRES_USER`/`POSTGRES_PASS` | `identity.serviceAccounts.titiler` | `uvicorn stac_higher_titiler.main:app --host 0.0.0.0 --port 8084 --proxy-headers --forwarded-allow-ips=*` |
| `tipg` | `catalog.tipg.image`, default `ghcr.io/developmentseed/tipg:1.0.1@sha256:70ae55…` (= compose `tipg`) | 8085 | image runs as root → 65534:65534 | `dbStacReader` → `POSTGRES_USER`/`POSTGRES_PASS` | `default`, no token | `uvicorn tipg.main:app --host 0.0.0.0 --port 8085 --proxy-headers --forwarded-allow-ips=*` |
| `cube-server` | chart key `cube-server` | 8086 | `cube` 999:999 | `dbCubeServer` → `PGUSER`/`PGPASSWORD` (libpq fallback for `CUBE_DB_DSN`) | `identity.serviceAccounts.cubeServer` | image CMD (`cube-server`) |

- Names: Deployment, Service and env ConfigMap `<fullname>-<component>` / `<fullname>-env-<component>`; Service port = container port, named `http`.
- Every container: R-6's hardened context (`readOnlyRootFilesystem: true`, no escalation, drop ALL), `HOME=/tmp`, `PYTHONDONTWRITEBYTECODE=1`, `/tmp` emptyDir (titiler and cube-server 1Gi for GDAL / numba caches, others 64Mi), the CA volume at `/etc/stac-higher/db-ca`, `PGSSLMODE=verify-full` + `PGSSLROOTCERT` when `verify-full` (libpq for titiler, the proxy policy and cube-server; asyncpg reads the same variables for stac-fastapi and tipg).
- `automountServiceAccountToken: false` on every pod. titiler and cube-server use their R-5 ServiceAccounts (rendered by `serviceaccounts.yaml`, IRSA annotation from `identity.roleArns.titiler` / `.cubeServer`, both required in `irsa` mode); the other three get no AWS identity.
- titiler carries `nodeSelector: {kubernetes.io/arch: amd64}` by default (the image is amd64 only, spec §5).
- Probes: readiness `httpGet` (`/_mgmt/ping`, `/healthz`, `/healthz`, `/healthz`, `/health`), liveness `tcpSocket`. Pod annotation `checksum/config` over the catalog ConfigMaps and `db-ca.yaml`.
- Each component has `replicas`, `resources`, `env` (extra non-secret env merged last; secret-looking keys refused as in R-6), `podAnnotations`, `nodeSelector`, `tolerations`, `affinity`.

### Env (non-secret; in `<fullname>-env-<component>`)

- `stac-api`: `PGHOST`, `PGPORT`, `PGDATABASE`, `ENABLE_TRANSACTIONS_EXTENSIONS=TRUE`, `WEB_CONCURRENCY=1`, `DB_MIN_CONN_SIZE=1`, `DB_MAX_CONN_SIZE=5`.
- `auth-proxy` (the enforced overlay, `infra/compose.auth-enforced.yml`, verbatim in substance): `UPSTREAM_URL=http://<fullname>-stac-api:8082`; `OIDC_DISCOVERY_URL=<auth.issuer>/.well-known/openid-configuration`; `OIDC_DISCOVERY_INTERNAL_URL` from `auth.issuerInternal` when set, else the same; `DEFAULT_PUBLIC=true`; `PRIVATE_ENDPOINTS` (the overlay's map); `ALLOWED_JWT_AUDIENCES` (`auth.audiences` comma-joined); `AUTH_BEARER_AUDIENCE_CLAIM`; `ITEMS_FILTER_CLS=stac_higher_proxy_policy:ExternallyWritableItemsFilter`; `ITEMS_FILTER_PATH` (the overlay's, routing `bulk_items`); `POLICY_DATABASE_URL=postgresql://<host>:<port>/<name>` (no userinfo); `POLICY_ROLES_CLAIM` and `POLICY_WRITE_ROLES` (below). `CATALOG_BFF_SHARED_SECRET` comes from the `platform` Secret.
- Role mapping for the proxy (ADR 0015 "role mapping in two places"): `catalog.authProxy.rolesClaim` if set, else `auth.claimsMapping.roles` when it is a string, else `realm_access.roles`. `catalog.authProxy.writeRoles` if non-empty, else the raw values the app maps to `operator`/`admin`: every `auth.claimsMapping.roleMap` key whose value is `operator` or `admin`, plus `operator` and `admin` themselves unless `roleMap` remaps them; sorted.
- `titiler`: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DBNAME`, `DB_MIN_CONN_SIZE=1`, `DB_MAX_CONN_SIZE=4`, the compose GDAL set (`CPL_TMPDIR=/tmp`, `GDAL_CACHEMAX=25%`, `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`, `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES`, `VSI_CACHE=TRUE`), `PLATFORM_ASSET_BUCKET=aws.bucket`, `ASSET_HREF_BASE=/api/assets`, `AWS_REGION`, `AWS_DEFAULT_REGION`. No `AWS_ACCESS_KEY_ID` (R-2: GDAL's own chain takes Pod Identity / IRSA).
- `tipg`: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DBNAME`, `DB_MIN_CONN_SIZE=1`, `DB_MAX_CONN_SIZE=4`.
- `cube-server`: `CUBE_DB_DSN=postgresql://<host>:<port>/<name>` (no userinfo), `CUBE_S3_ENDPOINT` = `objectStore.internalEndpoint` when set, else `publicUrls.objectStore`, `CUBE_S3_REGION=aws.region`, `PLATFORM_ASSET_BUCKET=aws.bucket`, `PORT=8086`. No `CUBE_S3_*` keys (default chain).
- App: `BUILTIN_CATALOG_URL` defaults to `http://<fullname>-auth-proxy:8000` when `app.builtinCatalogInternalUrl` is blank (its host joins `SAFE_FETCH_ALLOW_HOSTS`, as R-6 does for an explicit value). BFF writes therefore cross the proxy with `X-BFF-Auth` (ADR 0008/0015).

### Secrets

- `external-secrets` mode adds four **runtime** ExternalSecrets: `<fullname>-db-stac-api`, `-db-stac-reader`, `-db-proxy-policy`, `-db-cube-server` (JSON `username`/`password` of `secrets.arns.db_*`). `stac-higher.secretName` resolves those logical names to them; only `dbMaster` still resolves to its hook copy (no runtime pod reads it). `existing` mode is unchanged (one Secret per role, shared by hooks and runtime).

### Images

- `stac-higher.pullRef` is generalised to any ref (`stac-higher.mirrorRef`), so the third-party `ghcr.io/stac-utils/…` and `ghcr.io/developmentseed/…` refs take the same GHCR mirror prefix, digest unchanged.
- Third-party refs must carry `@sha256:<64 hex>`; the render fails otherwise (`catalog.stacApi.image`, `catalog.tipg.image`).
- With any `global.imageDigests` set, all eight keys are now required (titiler, cube-server and auth-proxy are referenced).
- ADR 0015 pairing: a render test asserts the chart's stac-fastapi default equals the `docker-compose.yml` `api` pin, the auth-proxy Dockerfile `FROM` equals the compose `auth-proxy` pin, and the tipg default equals the compose `tipg` pin, so a bump moves them together.

### Public hosts, Ingress, NetworkPolicy

- `publicUrls.catalog`, `.titiler`, `.tipg`, `.cubeServer` are bare origins like `publicUrls.app`; all five hosts must be distinct. Render fails otherwise, naming the key.
- One Ingress per host when `ingress.enabled`: `<fullname>-catalog` → auth-proxy, `<fullname>-titiler`, `<fullname>-tipg`, `<fullname>-cube-server`, path `/` Prefix. Annotations `ingress.<key>.annotations` merged over `ingress.annotations`; TLS from `ingress.<key>.tlsSecretName`, else `ingress.tlsSecretName`.
- NetworkPolicies (ingress only; egress stays open, spec §6.5/#68): `networkPolicy.publicIngressFrom` (default `null` → falls back to `appIngressFrom`; both null → any source) for proxy :8000, titiler :8084, tipg :8085, cube-server :8086; the proxy also admits the app pods; stac-api :8082 admits only the auth-proxy pods.
- `auth.issuer` is required in every auth mode now: the proxy needs an OIDC discovery URL.

### Rules

- Docs: FEATURES R-7 row; ISSUES entries from **I-165** under `## Deployment artifacts (R queue, epic #136)`; chart README; spec §6.1 gets one line recording the fit-check outcome with a link here; `docs/serving.md` and `docs/backend.md` pointers where they describe the compose-only catalog services.
- Commits `R-7: <what>`, trailer `Co-Authored-By: Claude Opus <noreply@anthropic.com>`.
- Gates: `scripts/check-helm.sh <dir>` (lint + template, every scenario), `cd app && npx vitest run src/__tests__/helm/`, kubeconform + trivy over the rendered output as `helm.yml` runs them, and `npm run verify` (the render suites live in `app/`).

## Review Focus

1. **No catalog workload reads a hook copy** (I-156): in both secrets modes, every `secretKeyRef` of the five new Deployments resolves to a runtime Secret. Test: Task 1 `catalog workloads read runtime Secrets only`, plus R-6's `Deployments reference no hook object` now covering seven Deployments.
2. **Each service logs in as its own role** (§7.2): stac-api → `db-stac-api`, titiler and tipg → `db-stac-reader`, proxy → `db-proxy-policy`, cube-server → `db-cube-server`; no `DATABASE_URL`/DSN carries userinfo. Test: Task 3 `each workload logs in as its own role`.
3. **No secret value in a ConfigMap**: the proxy's BFF secret and every password come only through `secretKeyRef`. Test: Task 2 `catalog ConfigMaps carry no credential`.
4. **Images**: the digest/mirror test covers the third-party refs; a bump of the stac-fastapi/proxy pair cannot drift from compose. Tests: Task 2.
5. **NetworkPolicy reachability**: stac-api is reachable only from the proxy; the app reaches the proxy even when public peers are restricted. Test: Task 4.

---

## File structure

```
infra/helm/stac-higher/
  values.yaml  values.schema.json  README.md            # new catalog.*, cubeServer.*, ingress.<svc>, networkPolicy.publicIngressFrom
  templates/_helpers.tpl                               # mirrorRef, secretName runtime roles, catalog deployment helper, proxy role derivation, validation
  templates/externalsecrets.yaml                       # four runtime role ExternalSecrets
  templates/serviceaccounts.yaml                       # + titiler, cube-server
  templates/config-env.yaml                            # BUILTIN_CATALOG_URL default
  templates/catalog-config.yaml                        # five env ConfigMaps
  templates/catalog-workloads.yaml                     # five Deployments + Services
  templates/catalog-ingress.yaml                       # four Ingresses
  templates/networkpolicies.yaml                       # + five policies
  tests/values/irsa.yaml                               # + titiler, cubeServer role ARNs
infra/examples/aws-eks/values.yaml                     # commented publicIngressFrom note
app/src/__tests__/helm/catalog.test.ts                 # new suite
app/src/__tests__/helm/{app,values,secrets,network,hooks}.test.ts   # adjusted for the new workloads
docs/FEATURES.md  docs/ISSUES.md  docs/backend.md  docs/serving.md
docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md   # §6.1 one-line fit-check record
```

---

### Task 1: Secrets and identity for the new workloads

**Files:** `templates/_helpers.tpl` (`secretName`), `templates/externalsecrets.yaml`, `templates/serviceaccounts.yaml`, `tests/values/irsa.yaml`, `templates/_helpers.tpl` (`validate`: irsa needs `roleArns.titiler` / `.cubeServer`). Tests: `secrets.test.ts`, `values.test.ts`.

- [ ] **Step 1: failing tests**
  - `secrets.test.ts`: external-secrets mode renders `stac-higher-db-stac-api`, `-db-stac-reader`, `-db-proxy-policy`, `-db-cube-server` (runtime, no hook annotation, `username`/`password` from the matching `secrets.arns.db_*`); the probe test's `runtime` name for those four logical names is `r-stac-higher-db-*` (not `-hook-`).
  - `values.test.ts`: ServiceAccounts `stac-higher-titiler` / `stac-higher-cube-server` exist, `automountServiceAccountToken: false`, IRSA annotation in `irsa`; `requires role ARNs in irsa mode` also names `identity.roleArns.titiler` and `.cubeServer`.
- [ ] **Step 2:** `cd app && npx vitest run src/__tests__/helm/secrets.test.ts src/__tests__/helm/values.test.ts` → FAIL.
- [ ] **Step 3: implement.**
- [ ] **Step 4:** same → PASS.
- [ ] **Step 5: commit** `R-7: runtime ExternalSecrets for the four service roles; titiler and cube-server ServiceAccounts`

### Task 2: Images, config ConfigMaps, validation

**Files:** `_helpers.tpl` (`mirrorRef`, `proxyRolesClaim`, `proxyWriteRoles`, `catalogChecksum`, `validate` additions), `templates/catalog-config.yaml`, `templates/config-env.yaml`, `values.yaml`, `values.schema.json`. Tests: `catalog.test.ts` (new), `values.test.ts`, `config.test.ts`.

- [ ] **Step 1: failing tests** (`catalog.test.ts` unless noted)
  - `catalog ConfigMaps carry no credential`: no key of any `env-*` ConfigMap matches `/PASSWORD|PASS$|SECRET|TOKEN|ACCESS_KEY/`, and no value matches `/\/\/[^/@]*@/`.
  - `the proxy runs the enforced write policy` (env equals the overlay's `ITEMS_FILTER_CLS`, `ITEMS_FILTER_PATH`, `PRIVATE_ENDPOINTS`, `DEFAULT_PUBLIC`, `ALLOWED_JWT_AUDIENCES`, `AUTH_BEARER_AUDIENCE_CLAIM`, discovery URLs, `UPSTREAM_URL`, credential-free `POLICY_DATABASE_URL`), parsed from `infra/compose.auth-enforced.yml` so the two cannot drift.
  - `proxy role mapping follows the app's claims mapping`: base (no roleMap) → `realm_access.roles` / `admin,operator`; the example's Cognito mapping → `cognito:groups` / `admin,operator,stac-admins,stac-operators`; explicit `catalog.authProxy.rolesClaim`/`writeRoles` win.
  - `cube-server env`: `CUBE_DB_DSN` without userinfo, `CUBE_S3_ENDPOINT` = public object store, or `objectStore.internalEndpoint` when set; no `CUBE_S3_ACCESS_KEY_ID`.
  - `titiler env carries the platform bucket and no static AWS key`.
  - `third-party images match the compose pins (ADR 0015 pair)`: chart defaults equal `docker-compose.yml`'s `api` and `tipg` images; `infra/proxy-policy/Dockerfile` `FROM` equals compose `auth-proxy`.
  - `refuses an unpinned third-party image` (`catalog.stacApi.image: …:6.3.1`) naming the key.
  - `values.test.ts`: `refuses partial digests` still names `pipeline`; a set lacking `titiler` fails naming it; `refuses a service URL with a path` and `refuses two services on one host`; `the catalog proxy needs auth.issuer` in bypass mode.
  - `config.test.ts`: `BUILTIN_CATALOG_URL` defaults to `http://stac-higher-auth-proxy:8000` and `SAFE_FETCH_ALLOW_HOSTS` includes `stac-higher-auth-proxy`; an explicit value still wins.
- [ ] **Step 2:** run those files → FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5: commit** `R-7: catalog config ConfigMaps, proxy role mapping, third-party image pins and validation`

### Task 3: The five Deployments and Services

**Files:** `_helpers.tpl` (`stac-higher.catalogDeployment` taking a spec dict), `templates/catalog-workloads.yaml`. Tests: `catalog.test.ts`, `app.test.ts` (digest/mirror test admits the two third-party refs), `hooks.test.ts` (≥ 7 Deployments).

- [ ] **Step 1: failing tests**
  - `each workload logs in as its own role` (both secrets modes): `effectiveEnv` user/password refs per the workload table; the proxy's `CATALOG_BFF_SHARED_SECRET` is `{secret: stac-higher-platform}`.
  - `catalog workloads read runtime Secrets only`: no `secretKeyRef` name contains `hook-`.
  - `pods are hardened with the image uid`: uid/gid per the table, `runAsNonRoot`, `seccompProfile`, container context, `/tmp` mount, `automountServiceAccountToken: false`, ServiceAccount names.
  - `titiler is scheduled on amd64 by default`.
  - `commands and probes`: commands per the table, readiness paths, liveness tcp, Service port = container port named `http`.
  - `config changes roll the catalog workloads` (checksum annotation changes with `catalog.titiler.env`).
  - `app.test.ts` `every pulled image is digest-pinned and mirrored`: chart images match the R-6 regex; third-party ones match `^000000000000\.dkr\.ecr\.us-east-1\.amazonaws\.com/ghcr/(stac-utils|developmentseed)/[a-z-]+:[^@]+@sha256:[0-9a-f]{64}$`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: commit** `R-7: stac-api, auth-proxy, titiler, tipg and cube-server Deployments and Services`

### Task 4: Ingress hosts and NetworkPolicies

**Files:** `templates/catalog-ingress.yaml`, `templates/networkpolicies.yaml`. Tests: `catalog.test.ts`, `network.test.ts`.

- [ ] **Step 1: failing tests**
  - `one Ingress per public host`: four Ingresses, hosts from the URLs (trailing slash trimmed), backends the right Service/port; annotations merged; per-host TLS override, global fallback; `ingress.enabled: false` renders none.
  - `network.test.ts`: `stac-api admits only the proxy`; `public services are open by default and follow publicIngressFrom, else appIngressFrom`; `the app reaches the proxy when public peers are restricted`; the `policies select the workloads' real labels` loop covers the five new policies.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: commit** `R-7: catalog Ingress hosts and NetworkPolicies`

### Task 5: CI scenarios, example values, README, docs, final gates

- [ ] README: values rows for the new keys (the README test enforces depth 2), a "Catalog plane" section (workload table, role per service, the proxy's enforced mode and role mapping, the fit-check outcome), Secrets table rows for the four runtime ExternalSecrets, "What later slices add" without R-7.
- [ ] `infra/examples/aws-eks/values.yaml`: commented `publicIngressFrom` beside `appIngressFrom`, and a commented `group.name` ALB annotation note (one ALB for five hosts).
- [ ] Docs: FEATURES R-7 row; ISSUES I-165+ (render-only catalog plane facts first exercised in R-11: asyncpg `PGSSLMODE`, tipg as uid 65534 on a read-only root, the BFF's internal-URL link hosts; URL-safe passwords for third-party clients in `existing` mode; tipg's `stac_reader` sees only `public` objects granted to PUBLIC, so a future vector table needs its own grant); spec §6.1 fit-check line; `docs/backend.md` / `docs/serving.md` pointers.
- [ ] Gates: `scripts/check-helm.sh "$TMPDIR/r7"` (all scenarios), the helm vitest suites, kubeconform 0.8.0 and trivy over the rendered files exactly as `helm.yml`, `npm run verify`; `git grep -nE '[0-9]{12}' -- infra/helm infra/examples/aws-eks/values.yaml | grep -v 000000000000` empty.
- [ ] **Commit** `R-7: chart README, example values, FEATURES/ISSUES/spec notes`

**PR (lead):** body starts `Closes #143`; states the fit-check result (fallback taken, evidence above); deviations: own templates instead of the eoapi-k8s subchart, `charts/` → `infra/helm/`, tipg vector grants deferred (no vector tables exist), `auth.issuer` required in bypass mode; lead-only: none until R-11.
