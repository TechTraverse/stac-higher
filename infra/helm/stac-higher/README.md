# stac-higher Helm chart

Tracking: epic #136. Spec:
[`../../../docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md`](../../../docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md)
§6; ADR [0024](../../../docs/decisions/0024-deployment-artifacts.md).

The Kubernetes side of a STAC Higher deployment: the app, the pipeline, the
catalog plane (stac-fastapi behind the auth-proxy, titiler, tipg), the
cube-server, the database hook Jobs, their configuration, Secrets wiring,
Ingresses and NetworkPolicies.
This repo publishes the chart only; it holds no cluster, account or values
for one. The AWS side is [`../../terraform/README.md`](../../terraform/README.md),
whose `helm_values` output this chart accepts as is. Chart `apiVersion: v2`;
it works on Helm 3 and 4 and needs Kubernetes 1.34 or later.

## Cluster prerequisites

- EKS 1.34+ with Auto Mode (managed Karpenter, built-in load balancing, the
  Pod Identity agent).
- NetworkPolicy enforcement enabled. The chart renders NetworkPolicies; without
  an enforcing CNI they are inert.
- Kueue v0.19+ with `waitForPodsReady` on and `managedJobsWithoutQueueName: false`.
  Both live in Kueue's own install configuration, which the chart cannot set.
  (R-6 renders no Kueue objects; later slices rely on this.)
- External Secrets Operator with a `ClusterSecretStore` for Secrets Manager,
  or Secrets the deployment creates itself (see Secrets modes).
- An IngressClass and TLS: an ACM ARN in annotations on the ALB, or cert-manager
  with `ingress.tlsSecretName`.
- Optional: the Prometheus Operator, for the `ServiceMonitor` behind
  `pipeline.metrics.serviceMonitor.enabled`.
- A dedicated namespace, equal to `identity.namespace` (default `stac-higher`);
  the render fails otherwise. The chart's NetworkPolicies start from
  default-deny ingress, so do not share the namespace with other workloads.
- The RDS CA bundle: a ConfigMap named in `database.caBundle.existingConfigMap`,
  or the PEM passed in `database.caBundle.pem`. With `sslMode: verify-full`
  exactly one of the two is required.

## Values

Every key at depth 2 or less. `null` means required. Section rows list the
section's own keys; the schema (`values.schema.json`) rejects unknown keys in
the closed sections (`aws`, `registry`, `publicUrls`, `objectStore`,
`database`, `identity`, `secrets.arns`, `secrets.existing`, `demoDestination`,
`demoSeed`).
Rendering fails with a message starting with the offending key path.

| Key | Default | Meaning |
|---|---|---|
| `nameOverride`, `fullnameOverride` | `""` | Standard overrides. The fullname is capped at 42 characters (the longest suffix appended is 21 and object names stop at 63); longer fails the render. |
| `global` | | Image selection. |
| `global.imageRepository` | `ghcr.io/techtraverse/stac-higher` | Upstream image is `<imageRepository>-<key>`. |
| `global.imageTag` | `""` | `""` means the chart's `appVersion`. |
| `global.imageDigests` | `{}` | The release manifest's `images.*.digest`, keys verbatim. When set, images pin by digest. |
| `global.imageRegistry` | `""` | The GHCR mirror prefix; replaces the leading `ghcr.io` of images the chart pulls. |
| `registry` | | ECR mirror information. |
| `registry.host` | `""` | The ECR registry host. Informational. |
| `registry.mirrorPrefixes` | `{}` | `ghcr.io`, `docker.io`, `quay.io` mapped to prefixes. The pull rewrite falls back to it when `global.imageRegistry` is blank. |
| `aws` | | AWS facts from Terraform. |
| `aws.partition` | `aws` | Informational. |
| `aws.region` | `us-east-1` | Becomes `AWS_REGION`, `AWS_DEFAULT_REGION`, `S3_REGION`, `STAGING_S3_REGION`. |
| `aws.bucket` | `null` | The platform bucket (`S3_BUCKET`, `STAGING_BUCKET`). |
| `aws.kmsKeyArn` | `""` | Informational; the bucket's default SSE-KMS applies. |
| `aws.processRunRoleArn` | `null` | `PROCESS_STS_ROLE_ARN`; its partition is also the run session policy's. |
| `aws.processMaxCredentialSeconds` | `3600` | Integer >= 900; `PROCESS_MAX_CREDENTIAL_SECONDS`. An explicit `null` removes the cap. Hardware profiles whose queue wait plus grace cannot fit under it fail the render. |
| `aws.s3PrefixListCidrs` | `[]` | Consumed by R-8. |
| `publicUrls` | | Browser-facing URLs. |
| `publicUrls.app` | `null` | `http(s)://host` with no path. Derives the Ingress host and the OIDC redirect. |
| `publicUrls.objectStore` | `null` | `https://s3.<region>.<suffix>`; the app's `S3_ENDPOINT` only. |
| `publicUrls.catalog`, `publicUrls.titiler`, `publicUrls.tipg`, `publicUrls.cubeServer` | `null` | Public service URLs; deployment-supplied (R-5 does not emit them). |
| `objectStore` | | S3 client behaviour. |
| `objectStore.forcePathStyle` | `false` | Path-style addressing (MinIO). |
| `objectStore.internalEndpoint` | `""` | `STAGING_S3_ENDPOINT` and `PROCESS_RUN_S3_ENDPOINT`; also the cube-server's `CUBE_S3_ENDPOINT` and titiler's `AWS_S3_ENDPOINT`. Blank on AWS (the STS client derives its endpoint from it); set for MinIO only. |
| `database` | | The RDS instance. |
| `database.host`, `database.name` | `null` | Required. |
| `database.port` | `5432` | |
| `database.masterSecretArn` | `""` | The RDS-managed master secret (JSON `username`/`password`); required in `external-secrets` mode. |
| `database.caCertIdentifier` | `""` | Informational: names the CA bundle the deployment must supply. |
| `database.sslMode` | `verify-full` | `verify-full` or `disable` (dev only). |
| `database.caBundle` | | `existingConfigMap` (name), `key` (default `ca-bundle.pem`) and `pem` (inline text; the chart renders the ConfigMap). |
| `auth` | | Authentication. |
| `auth.mode` | `oidc` | `oidc` or `bypass`. |
| `auth.allowDevBypass` | `false` | `bypass` fails to render without it. |
| `auth.issuer`, `auth.clientId` | `null` | `clientId` is required with `oidc`. `issuer` is required in every mode: the catalog proxy's startup health-checks its discovery URL (upstream `WAIT_FOR_UPSTREAM`) and fails without it, even pass-through. |
| `auth.issuerInternal` | `""` | `OIDC_ISSUER_INTERNAL`. |
| `auth.audiences` | `[stac-higher]` | Comma-joined into `AUTH_BEARER_AUDIENCES`. |
| `auth.audienceClaim` | `aud` | `aud` or `client_id`. |
| `auth.claimsMapping` | `null` | Object, rendered as compact JSON `AUTH_CLAIMS_MAPPING`. |
| `auth.hostedDomain` | `""` | Informational. |
| `auth.devIdentity` | `null` | `DEV_AUTH_IDENTITY` (bypass only). |
| `identity` | | Workload identity. |
| `identity.mode` | `pod-identity` | `pod-identity` or `irsa`. |
| `identity.namespace` | `stac-higher` | The release namespace must equal it. |
| `identity.serviceAccounts` | `stac-higher-app`, `-pipeline`, `-titiler`, `-cube-server` | ServiceAccount names (keys `app`, `pipeline`, `titiler`, `cubeServer`). The chart names its ServiceAccounts from these only. |
| `identity.roleArns` | all `""` | Role ARNs; `app` and `pipeline` are required with `irsa` (`eks.amazonaws.com/role-arn`). |
| `secrets` | | Secrets wiring; see Secrets modes. |
| `secrets.mode` | `external-secrets` | `external-secrets` or `existing`. |
| `secrets.arns` | ten keys, each `""` | Terraform's secret ARNs, used as ESO remote keys. |
| `secrets.dockerHubArn` | `""` | Optional Docker Hub credential (JSON `username`/`token`). |
| `secrets.externalSecrets` | | `storeRef.name` (required), `storeRef.kind` (`ClusterSecretStore` or `SecretStore`), `refreshInterval` (`1h`). |
| `secrets.existing` | all `""` | Secret names for `existing` mode. |
| `demoDestination` | | The demo's destination bucket (R-5's `demo-destination` module); read only by the demo seed Job with `demoSeed.deliver`. |
| `demoDestination.bucket`, `demoDestination.region`, `demoDestination.credentialsSecretArn` | `""` | Bucket, its region (blank: `aws.region`), and the Secrets Manager entry holding the write-only user's key (JSON `access_key_id`/`secret_access_key`). |
| `processRuns` | | Process-run settings. |
| `processRuns.namespace` | `stac-higher-runs` | R-8 creates it. |
| `processRuns.networkMax` | `isolated` | `PROCESS_NETWORK_MAX` (app and pipeline). |
| `processRuns.credentialGraceSeconds` | `300` | `PROCESS_CREDENTIAL_GRACE_SECONDS` (app and pipeline). |
| `processRuns.clusterObjects` | `true` | Consumed by R-8. |
| `config` | | Content for the config ConfigMap. |
| `config.hardwareProfiles` | `null` | `null` uses the chart's `files/hardware-profiles/eks-cpu.json`. |
| `config.imagePolicy` | `null` | `null` uses `files/image-policy/default.json`. |
| `ingress` | | One Ingress per public host: the app, the catalog (auth-proxy), titiler, tipg, the cube-server. |
| `ingress.enabled` | `true` | Renders all five. |
| `ingress.className` | `""` | `ingressClassName`. |
| `ingress.annotations` | `{}` | On every chart Ingress. On the ALB, `alb.ingress.kubernetes.io/group.name` puts the five hosts on one load balancer. |
| `ingress.tlsSecretName` | `""` | Set: `spec.tls` (cert-manager). The ALB uses annotations. The four catalog hosts fall back to it; a shared name suits a pre-made wildcard Secret, while cert-manager's ingress-shim wants one per host. |
| `ingress.app` | | `annotations`, merged over `ingress.annotations`. |
| `ingress.catalog`, `ingress.titiler`, `ingress.tipg`, `ingress.cubeServer` | | `annotations` (merged over `ingress.annotations`) and `tlsSecretName` (`""` uses `ingress.tlsSecretName`). Hosts come from `publicUrls.*`. |
| `networkPolicy` | | |
| `networkPolicy.enabled` | `true` | |
| `networkPolicy.appIngressFrom` | `null` | `null` opens the app port to any source; otherwise a list of NetworkPolicy peers. |
| `networkPolicy.publicIngressFrom` | `null` | Peers for the catalog proxy, titiler, tipg and cube-server ports; `null` or `[]` uses `appIngressFrom` (so an empty list never opens ports that `appIngressFrom` restricts); both empty: any source. The proxy also admits the app pods; stac-api admits only the proxy. |
| `networkPolicy.metricsFrom` | `[]` | Peers allowed to reach pipeline `:8083`. |
| `app` | | The app Deployment. |
| `app.replicas` | `2` | Omitted when the HPA is on. |
| `app.autoscaling` | off, 2 to 6, 70% CPU | `enabled`, `minReplicas`, `maxReplicas`, `targetCPUUtilizationPercentage`. |
| `app.resources` | 250m / 512Mi, memory limit 1Gi | |
| `app.readOnlyRootFilesystem` | `true` | Escape hatch (I-161). |
| `app.builtinCatalogInternalUrl` | `""` | `BUILTIN_CATALOG_URL`, where BFF writes go; `""` means the in-cluster proxy, `http://<fullname>-auth-proxy:8000`. Its host joins `SAFE_FETCH_ALLOW_HOSTS`. |
| `app.safeFetchAllowHosts` | `[]` | Extra allowed hosts. |
| `app.env` | `{}` | Extra non-secret env. |
| `app.podAnnotations`, `app.nodeSelector`, `app.tolerations`, `app.affinity` | empty | Standard scheduling and metadata. |
| `pipeline` | | The pipeline Deployment (one replica, `Recreate`). |
| `pipeline.resources` | 500m / 1Gi, memory limit 2Gi | |
| `pipeline.readOnlyRootFilesystem` | `true` | Escape hatch (I-161). |
| `pipeline.egressAllowHosts` | `[]` | `EGRESS_ALLOW_HOSTS`. |
| `pipeline.env`, `pipeline.podAnnotations`, `pipeline.nodeSelector`, `pipeline.tolerations`, `pipeline.affinity` | empty | As for `app`. |
| `pipeline.metrics` | | `serviceMonitor.enabled` (`false`), `.interval` (`30s`), `.labels`. |
| `catalog` | | The catalog plane (see Catalog plane). Each component takes `replicas`, `resources`, `env` (extra non-secret env, merged last; secret-looking keys fail the render), `podAnnotations`, `nodeSelector`, `tolerations`, `affinity`. |
| `catalog.stacApi` | 2 replicas, 250m / 512Mi, limit 1Gi | stac-fastapi-pgstac. `image` is the third-party ref, pinned by digest (the render refuses one without); keep it equal to `docker-compose.yml`'s `api` (ADR 0015 pair). |
| `catalog.authProxy` | 2 replicas, 100m / 256Mi, limit 512Mi | The derived auth-proxy. `rolesClaim` (`""`: `auth.claimsMapping.roles`, else `realm_access.roles`) and `writeRoles` (`[]`: derived from `auth.claimsMapping.roleMap`). |
| `catalog.titiler` | 2 replicas, 500m / 1Gi, limit 2Gi | The derived titiler. `nodeSelector` defaults to `kubernetes.io/arch: amd64` (the image is amd64 only). |
| `catalog.tipg` | 1 replica, 250m / 512Mi, limit 1Gi | tipg. `image` is the pinned third-party ref (= compose `tipg`). |
| `cubeServer` | | The cube-server. |
| `cubeServer.replicas` | `1` | |
| `cubeServer.resources` | 500m / 1Gi, limit 4Gi | |
| `cubeServer.env` | `{}` | Extra non-secret env, e.g. `CUBE_POLL_SECONDS`, `CUBE_CORS_ORIGINS`. |
| `cubeServer.podAnnotations`, `cubeServer.nodeSelector`, `cubeServer.tolerations`, `cubeServer.affinity` | empty | |
| `dbJobs` | | The database hook Jobs. |
| `dbJobs.backoffLimit`, `dbJobs.activeDeadlineSeconds` | `2`, `900` | |
| `dbJobs.resources` | 100m / 256Mi, memory limit 1Gi | |
| `dbJobs.nodeSelector`, `dbJobs.tolerations`, `dbJobs.affinity` | empty | |
| `demoSeed` | | The one-shot demo seed Job (see Demo seed). |
| `demoSeed.enabled` | `false` | Render the Job. |
| `demoSeed.seeds` | `[seed, goes-seed]` | Any of `seed`, `goes-seed`, `cube-seed`, `images-seed`; always run in that order. |
| `demoSeed.stacUrl` | `""` | In-cluster stac-fastapi URL the seeds write collections to (the compose `:8082` equivalent). Required when enabled. |
| `demoSeed.deliver` | `false` | `goes-seed --deliver` to `demoDestination`. Needs `goes-seed`, `demoDestination.bucket` and the key source for the secrets mode. |
| `demoSeed.existingDestinationSecret` | `""` | `existing` mode: Secret with `access_key_id`/`secret_access_key`. In `external-secrets` mode the chart renders `<fullname>-demo-destination` from `demoDestination.credentialsSecretArn`. |
| `demoSeed.appBearer` | | `secretName` (required with `images-seed`) and `key` (`token`): an operator bearer token for the app API. |
| `demoSeed.extraArgs` | `{}` | Extra CLI args per chosen seed, e.g. `{goes-seed: ["--window", "-2h"]}`. |
| `demoSeed.waitReadySeconds` | `600` | Each seed waits up to this long for the STAC API (and, for `images-seed`, the app) to answer with a 2xx-4xx status before it writes (`--wait-ready`; a 5xx or a broken connection is not ready). |
| `demoSeed.backoffLimit`, `demoSeed.activeDeadlineSeconds` | `0`, `3600` | No pod retries by default: a retry would redo every seed that already succeeded. The deadline covers `images-seed`'s scan wait. |
| `demoSeed.resources` | 100m / 512Mi, memory limit 1Gi | Per seed container. |

## Secrets modes

The chart never renders a `kind: Secret`, and no value file carries a secret.
There are two modes (`secrets.mode`).

**`external-secrets`** (default). The chart renders ExternalSecrets against
`secrets.externalSecrets.storeRef`, with the Terraform `secrets` module's
output as remote keys:

| Terraform key (`secrets.arns.*`) | Becomes |
|---|---|
| `db_stac_higher_owner` | `<fullname>-db-owner` (`username`, `password`) and a hook-scoped copy `<fullname>-hook-db-owner` |
| `db_stac_api`, `db_stac_reader`, `db_stac_proxy_policy`, `db_cube_server` | runtime `<fullname>-db-stac-api`, `-db-stac-reader`, `-db-proxy-policy`, `-db-cube-server` (`username`, `password`), read by the catalog plane and the cube-server; and hook-scoped copies `<fullname>-hook-db-stac-api`, `-hook-db-stac-reader`, `-hook-db-proxy-policy`, `-hook-db-cube-server` (with `-hook-db-owner` and `-hook-db-master`, the six hook Secrets), read only by the database Jobs |
| `session_secret`, `catalog_bff_shared_secret`, `internal_api_token`, `credentials_master_key` | `<fullname>-platform` (`SESSION_SECRET`, `CATALOG_BFF_SHARED_SECRET`, `INTERNAL_API_TOKEN`, `CREDENTIALS_MASTER_KEY`) |
| `proxy_auth_token` | accepted and ignored (I-159) |
| `database.masterSecretArn` (the RDS master) | hook-scoped `<fullname>-hook-db-master` |
| `secrets.dockerHubArn` (optional) | `<fullname>-dockerhub` (`username`, `token`) |

All ten `secrets.arns` keys but `proxy_auth_token`, `database.masterSecretArn`
and `secrets.externalSecrets.storeRef.name` are required in this mode.

**`existing`**. The deployment creates the Secrets and names them in
`secrets.existing.*`. All but `dockerHub` are required. Layouts:

- database Secrets (`dbMaster`, `dbOwner`, `dbStacApi`, `dbStacReader`,
  `dbProxyPolicy`, `dbCubeServer`): keys `username` and `password`. The role
  name is the Secret's `username`.
- `platform`: keys `SESSION_SECRET`, `CATALOG_BFF_SHARED_SECRET`,
  `INTERNAL_API_TOKEN`, `CREDENTIALS_MASTER_KEY`.
- `dockerHub`: keys `username` and `token`.

Dev recipe, which writes nothing into values (repeat the first command for
each of `db-master`, `db-owner`, `db-stac-api`, `db-stac-reader`,
`db-proxy-policy`, `db-cube-server`, choosing the role names yourself):

```sh
kubectl -n stac-higher create secret generic db-owner \
  --from-literal=username=<role-name> --from-literal=password=<password>
kubectl -n stac-higher create secret generic platform \
  --from-literal=SESSION_SECRET=<value> \
  --from-literal=CATALOG_BFF_SHARED_SECRET=<value> \
  --from-literal=INTERNAL_API_TOKEN=<value> \
  --from-literal=CREDENTIALS_MASTER_KEY=<value>
```

Then set `secrets.mode: existing` and the matching `secrets.existing.*` names.

## Database jobs

Three Jobs run as pre-install and pre-upgrade Helm hooks and as Argo CD
`PreSync` hooks (Flux uses Helm's hooks). They re-run on every upgrade or
sync, so their SQL is idempotent, and rotated passwords are applied.

| Weight | Object | Runs as | Does |
|---|---|---|---|
| -30 | hook ConfigMaps and hook ExternalSecrets | | The SQL files, the CA bundle if inline, and the credential copies. |
| -20 | `<fullname>-db-bootstrap` | the RDS master (`dbMaster`) | Creates or updates the five login roles, the extensions, `stac_higher_cube_reader`, and the owner's grants. |
| -15 | `<fullname>-pgstac-migrate` | the owner (`dbOwner`) | `pypgstac migrate`, then `GRANT pgstac_ingest TO` the API role and `pgstac_read TO` the reader. |
| -10 | `<fullname>-app-migrate` | the owner (`dbOwner`) | The app's migrations, then the proxy role's grants. |

The Jobs run the pipeline and app images; the SQL goes through the pipeline
image's Python, not `psql`. Notes:

- Role passwords are sent to Postgres as client-side SCRAM-SHA-256 verifiers,
  never as plaintext SQL. A verifier can still appear in the server log under
  `log_statement=all`.
- The RDS master's hook Secret is deleted once the hook phase succeeds. The
  five role Secrets' hook copies are recreated on every hook phase.
- Bootstrap refuses a role name equal to the connecting role, or shared by two
  roles.
- The chart's fullname is capped at 42 characters; longer fails the render.
- Hook objects carry the label `app.kubernetes.io/component=db-hook`.

## Catalog plane and cube-server

The chart runs the catalog plane from its own templates. eoapi-k8s was fit-checked
as a subchart and not adopted: its external database is one login for every
service, its stac-auth-proxy subchart cannot read env from a Secret, and its
images cannot follow `global.imageDigests` or the mirror rewrite (evidence:
[`docs/superpowers/plans/2026-11-24-r7-chart-catalog-plane.md`](../../../docs/superpowers/plans/2026-11-24-r7-chart-catalog-plane.md)).

| Workload | Image | Port | Runs as | Logs in as (runtime Secret) | Public host |
|---|---|---|---|---|---|
| `<fullname>-auth-proxy` | `auth-proxy` (derived, ADR 0015) | 8000 | 1001 | `dbProxyPolicy`, read-only; plus `CATALOG_BFF_SHARED_SECRET` from `platform` | `publicUrls.catalog` |
| `<fullname>-stac-api` | `catalog.stacApi.image` | 8082 | 1000 | `dbStacApi` (member of `pgstac_ingest`) | none: only the proxy reaches it |
| `<fullname>-titiler` | `titiler` (derived) | 8084 | 1001:999 | `dbStacReader` (member of `pgstac_read`) | `publicUrls.titiler` |
| `<fullname>-tipg` | `catalog.tipg.image` | 8085 | 65534 | `dbStacReader` | `publicUrls.tipg` |
| `<fullname>-cube-server` | `cube-server` | 8086 | 999 | `dbCubeServer` (member of `stac_higher_cube_reader`) | `publicUrls.cubeServer` |

- Every public URL is a bare origin on its own host; the render fails otherwise.
- Probes: a startup probe on the readiness path allows up to 5 minutes (the
  proxy waits for stac-api and the IdP and checks conformance; the others open
  database pools), then readiness on that path and liveness on the port. Not
  values-tunable, like the app's and pipeline's.
- Database URLs carry no credentials: logins arrive as `PGUSER`/`PGPASSWORD`
  (or `POSTGRES_USER`/`POSTGRES_PASS` for titiler and tipg) from the runtime
  Secrets, and TLS from `PGSSLMODE`/`PGSSLROOTCERT`. titiler, tipg and
  stac-fastapi build their own connection URL from those values, so in
  `existing` mode give these roles URL-safe passwords (the Terraform module
  generates alphanumeric ones).
- Only titiler and the cube-server have an AWS identity (their R-5
  ServiceAccounts); no catalog pod mounts an API token. No static AWS key is set.
- Object store: on AWS both use the regional S3 endpoint and the default
  credential chain. With `objectStore.internalEndpoint` set (an S3-compatible
  store), the cube-server gets it as `CUBE_S3_ENDPOINT` and titiler as GDAL's
  `AWS_S3_ENDPOINT`/`AWS_HTTPS` (plus `AWS_VIRTUAL_HOSTING=FALSE` with
  `objectStore.forcePathStyle`). The chart sets no static keys, so such a store
  must accept the pods' ambient credentials.
- With `auth.mode: bypass` the proxy runs pass-through, as `docker-compose.yml`'s
  base file runs it (`PRIVATE_ENDPOINTS={}`, no write policy): the app's BFF
  sends writes without a token in bypass, which an enforced proxy would refuse.
  It still needs `auth.issuer`, because upstream stac-auth-proxy requires
  `OIDC_DISCOVERY_URL` and health-checks it at startup.
- With `auth.mode: oidc` the proxy runs the auth-enforced write policy exactly as
  `infra/compose.auth-enforced.yml` configures it: reads public, transactions
  need a token with the `auth.audiences` audience, item writes need the BFF
  secret or an operator/admin role on an externally writable collection.
  `POLICY_ROLES_CLAIM` and `POLICY_WRITE_ROLES` are derived from
  `auth.claimsMapping` (the raw values the app maps to `operator`/`admin`) unless
  `catalog.authProxy.rolesClaim` / `writeRoles` are set. `auth.issuer` is
  therefore required in every auth mode.
- The proxy accepts tokens from one issuer only: `OIDC_ISSUER` is `auth.issuer`
  exactly as given (a trailing `/` is kept). With `auth.issuerInternal` the
  proxy fetches discovery and keys in-network but still requires the public
  issuer in `iss`, so a token minted at the internal URL is refused
  (`docs/auth.md`).
- The app's BFF writes go to the proxy inside the cluster
  (`BUILTIN_CATALOG_URL=http://<fullname>-auth-proxy:8000`).
- The two third-party images are pinned by digest and take the GHCR mirror
  prefix like the chart's own; bump stac-fastapi together with the auth-proxy
  base (ADR 0015).

## Demo seed

Off by default. With `demoSeed.enabled` the chart renders one Job that runs
`python -m pipeline.demo --in-cluster <seed>` for each chosen seed, one
container each (all but the last as init containers), in the fixed order
`seed`, `goes-seed`, `cube-seed`, `images-seed`. What each seed builds and
what in-cluster mode changes:
[`../../../services/pipeline/src/pipeline/demo/README.md`](../../../services/pipeline/src/pipeline/demo/README.md).

- **Not a hook.** It needs the migrated database and the running catalog
  plane. Each seed first waits up to `demoSeed.waitReadySeconds` for the
  STAC API (and, for `images-seed`, the app) to answer, so the Job can start
  beside a catalog plane that is still coming up.
- **No retries by default** (`demoSeed.backoffLimit: 0`). A pod retry starts
  from the first seed again, so it would redo every seed that already
  succeeded, and each one replaces its processes' revisions and run history.
  A failed Job stays failed with its logs; the seeds before the failing one
  have completed.
- **Runs once per pod spec.** The Job name ends in a hash of its pod
  template, and that hash input is the very template the Job carries, so the
  name and the (immutable) `spec.template` only ever change together. The pod
  carries selector labels only; the chart version and appVersion labels sit
  on the Job's own metadata, which may change. A chart-only release (new
  chart version or appVersion, same image digests) therefore leaves the
  completed Job and its template untouched, while a new image, new seeds, or
  new args, env or Secret references create a new Job (the old one is pruned)
  and re-seed. With tags
  instead of digests the image tag defaults to the appVersion, so a new
  appVersion is a new image and re-seeds. Re-seeding replaces the demo
  processes' revisions and their run history. To re-run by hand, delete the
  Job and upgrade or sync. The Job sets no `ttlSecondsAfterFinished`,
  because a GitOps controller would recreate a Job that vanished.
- **Identity and Secrets.** The pod runs the pipeline image as the pipeline
  ServiceAccount (its workload identity writes `seed`'s scene to the platform
  bucket), reads the pipeline's env ConfigMaps and the runtime `db-owner`
  Secret (never hook copies). `CREDENTIALS_MASTER_KEY` reaches only
  `goes-seed` and `cube-seed` (the two that seal connection credentials), the
  destination key only `goes-seed`, the bearer only `images-seed`.
- **Writes bypass the app.** Like the compose seeds, the Job writes catalog
  documents straight to stac-fastapi and platform rows straight to the
  database as the owner role, not through the BFF, so no `audit_log` rows are
  written. Accepted for an operator-run demo Job (I-169).
- **Network.** The pod only makes egress. It carries
  `app.kubernetes.io/component=demo-seed`. The STAC API's ingress policy must
  admit it (R-7). When `networkPolicy.appIngressFrom` restricts the app and
  `images-seed` is chosen, the chart adds the seed pod as a peer on the app's
  policy.
- **`images-seed` before the Kubernetes executor.** Process runs (the
  extractor, GeoColor, image scans) need K-5 (I-160). Until then the other
  seeds complete but the runs they trigger fail at launch, and `images-seed`
  waits out its scan timeout and fails the Job. Leave it out of
  `demoSeed.seeds` until K-5. See `docs/ISSUES.md` I-169.

## Install paths

Every release publishes the chart at `oci://ghcr.io/<owner>/charts/stac-higher`,
version = the release tag without `v`. Pinning a release (chart, image digests
and Terraform module ref) from a GitHub or GitLab pipeline:
[`docs/releases.md`](../../../docs/releases.md), "Consuming a release".

Helm, with the digests from a release manifest (`docs/releases.md`, "Feeding
the chart"):

```sh
jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml

helm upgrade --install stac-higher oci://ghcr.io/<owner>/charts/stac-higher \
  --version <v> -n stac-higher --create-namespace \
  -f values.yaml -f digests.values.yaml --timeout 15m
```

With `demoSeed.enabled`, do not add `--wait-for-jobs` or `--atomic` (Helm 4:
`--rollback-on-failure`). The demo seed Job can legitimately run for tens of
minutes, or fail before K-5 (see Demo seed), and either flag would then hold
the release or roll it back over a demo. Whether Helm 4's `--wait` alone
waits for Jobs was not verified; treat it as a caution and check before
relying on it.

Argo CD (multi-source, so the value files come from your deployment repo
rather than from inside the chart archive; `repoURL` for the chart must be
registered as an OCI Helm repository). The chart's `helm.sh/hook` annotations are mapped to Argo hooks
(`pre-install`/`pre-upgrade` become `PreSync`), and the chart also carries
Argo's own annotations with the same weights as sync waves:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: stac-higher
  namespace: argocd
spec:
  project: default
  destination:
    server: https://kubernetes.default.svc
    namespace: stac-higher
  sources:
    - repoURL: ghcr.io/<owner>/charts   # an OCI-enabled Helm repository entry in Argo CD
      chart: stac-higher
      targetRevision: <v>
      helm:
        valueFiles:
          - $values/deploy/values.yaml
          - $values/deploy/digests.values.yaml
    - repoURL: https://github.com/<org>/<deployment-repo>.git
      targetRevision: main
      ref: values
  syncPolicy:
    syncOptions: [CreateNamespace=true]
```

Flux:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: OCIRepository
metadata:
  name: stac-higher
  namespace: stac-higher
spec:
  interval: 10m
  url: oci://ghcr.io/<owner>/charts/stac-higher
  ref:
    tag: <v>
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: stac-higher
  namespace: stac-higher
spec:
  interval: 10m
  timeout: 15m
  chartRef:
    kind: OCIRepository
    name: stac-higher
  valuesFrom:
    - kind: ConfigMap
      name: stac-higher-values
```

`infra/examples/aws-eks/values.yaml` shows a values file for the placeholder
AWS deployment.

## Images

Each image is `<global.imageRepository>-<key>`, by tag (`global.imageTag`, or
the chart's `appVersion`) or, when `global.imageDigests` is set, by digest.
Digests are validated: known keys only (`app`, `pipeline`, `titiler`,
`cube-server`, `image-scanner`, `process-runtime`, `process-runtime-stactools`,
`auth-proxy`), each `sha256:` plus 64 hex characters, and a non-empty set must
cover all eight (every one is referenced). The two third-party images
(`catalog.stacApi.image`, `catalog.tipg.image`) are full refs that must carry
their own `@sha256:` digest.

The mirror rewrite applies to every image the chart pulls, the third-party
ones included: `ghcr.io` becomes
`global.imageRegistry` (or `registry.mirrorPrefixes["ghcr.io"]`), and other
hosts use their `registry.mirrorPrefixes` entry, with trailing slashes trimmed
and digests unchanged. The runtime image refs in the environment
(`PROCESS_RUNTIME_IMAGE`, `PROCESS_RUNTIME_IMAGE_STACTOOLS`,
`IMAGE_SCANNER_IMAGE`) keep the upstream form, because image rows store
upstream refs and the executor maps the pull.

## Uninstall

`helm uninstall` leaves the hook objects behind. Remove them with:

```sh
kubectl -n stac-higher delete configmap,externalsecret,job,secret -l app.kubernetes.io/component=db-hook
```

## What later slices add

- R-8: the process-run namespace, RBAC, Kueue and NodePool objects, and the runs NetworkPolicy.
