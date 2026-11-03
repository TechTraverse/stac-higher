# stac-higher Helm chart

Tracking: epic #136. Spec:
[`../../../docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md`](../../../docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md)
§6; ADR [0024](../../../docs/decisions/0024-deployment-artifacts.md).

The Kubernetes side of a STAC Higher deployment: the app, the pipeline, the
database hook Jobs, their configuration, Secrets wiring and NetworkPolicies.
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
`database`, `identity`, `secrets.arns`, `secrets.existing`, `demoDestination`).
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
| `aws.processRunRoleArn` | `null` | `PROCESS_STS_ROLE_ARN`. |
| `aws.processMaxCredentialSeconds` | `3600` | Integer >= 900; `PROCESS_MAX_CREDENTIAL_SECONDS`. An explicit `null` removes the cap. Hardware profiles whose queue wait plus grace cannot fit under it fail the render. |
| `aws.s3PrefixListCidrs` | `[]` | Consumed by R-8. |
| `publicUrls` | | Browser-facing URLs. |
| `publicUrls.app` | `null` | `http(s)://host` with no path. Derives the Ingress host and the OIDC redirect. |
| `publicUrls.objectStore` | `null` | `https://s3.<region>.<suffix>`; the app's `S3_ENDPOINT` only. |
| `publicUrls.catalog`, `publicUrls.titiler`, `publicUrls.tipg`, `publicUrls.cubeServer` | `null` | Public service URLs; deployment-supplied (R-5 does not emit them). |
| `objectStore` | | S3 client behaviour. |
| `objectStore.forcePathStyle` | `false` | Path-style addressing (MinIO). |
| `objectStore.internalEndpoint` | `""` | `STAGING_S3_ENDPOINT` and `PROCESS_RUN_S3_ENDPOINT`. Blank on AWS (the STS client derives its endpoint from it); set for MinIO only. |
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
| `auth.issuer`, `auth.clientId` | `null` | Required with `oidc`. |
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
| `demoDestination` | | The demo's destination bucket; consumed by R-10. |
| `demoDestination.bucket`, `demoDestination.region`, `demoDestination.credentialsSecretArn` | `""` | Consumed by R-10. |
| `processRuns` | | Process-run settings. |
| `processRuns.namespace` | `stac-higher-runs` | R-8 creates it. |
| `processRuns.networkMax` | `isolated` | `PROCESS_NETWORK_MAX` (app and pipeline). |
| `processRuns.credentialGraceSeconds` | `300` | `PROCESS_CREDENTIAL_GRACE_SECONDS` (app and pipeline). |
| `processRuns.clusterObjects` | `true` | Consumed by R-8. |
| `config` | | Content for the config ConfigMap. |
| `config.hardwareProfiles` | `null` | `null` uses the chart's `files/hardware-profiles/eks-cpu.json`. |
| `config.imagePolicy` | `null` | `null` uses `files/image-policy/default.json`. |
| `ingress` | | The app Ingress. |
| `ingress.enabled` | `true` | |
| `ingress.className` | `""` | `ingressClassName`. |
| `ingress.annotations` | `{}` | On every chart Ingress. |
| `ingress.tlsSecretName` | `""` | Set: `spec.tls` (cert-manager). The ALB uses annotations. |
| `ingress.app` | | `annotations`, merged over `ingress.annotations`. |
| `networkPolicy` | | |
| `networkPolicy.enabled` | `true` | |
| `networkPolicy.appIngressFrom` | `null` | `null` opens the app port to any source; otherwise a list of NetworkPolicy peers. |
| `networkPolicy.metricsFrom` | `[]` | Peers allowed to reach pipeline `:8083`. |
| `app` | | The app Deployment. |
| `app.replicas` | `2` | Omitted when the HPA is on. |
| `app.autoscaling` | off, 2 to 6, 70% CPU | `enabled`, `minReplicas`, `maxReplicas`, `targetCPUUtilizationPercentage`. |
| `app.resources` | 250m / 512Mi, memory limit 1Gi | |
| `app.readOnlyRootFilesystem` | `true` | Escape hatch (I-161). |
| `app.builtinCatalogInternalUrl` | `""` | `BUILTIN_CATALOG_URL`; its host joins `SAFE_FETCH_ALLOW_HOSTS`. R-7 sets it. |
| `app.safeFetchAllowHosts` | `[]` | Extra allowed hosts. |
| `app.env` | `{}` | Extra non-secret env. |
| `app.podAnnotations`, `app.nodeSelector`, `app.tolerations`, `app.affinity` | empty | Standard scheduling and metadata. |
| `pipeline` | | The pipeline Deployment (one replica, `Recreate`). |
| `pipeline.resources` | 500m / 1Gi, memory limit 2Gi | |
| `pipeline.readOnlyRootFilesystem` | `true` | Escape hatch (I-161). |
| `pipeline.egressAllowHosts` | `[]` | `EGRESS_ALLOW_HOSTS`. |
| `pipeline.env`, `pipeline.podAnnotations`, `pipeline.nodeSelector`, `pipeline.tolerations`, `pipeline.affinity` | empty | As for `app`. |
| `pipeline.metrics` | | `serviceMonitor.enabled` (`false`), `.interval` (`30s`), `.labels`. |
| `dbJobs` | | The database hook Jobs. |
| `dbJobs.backoffLimit`, `dbJobs.activeDeadlineSeconds` | `2`, `900` | |
| `dbJobs.resources` | 100m / 256Mi, memory limit 1Gi | |
| `dbJobs.nodeSelector`, `dbJobs.tolerations`, `dbJobs.affinity` | empty | |
| `demoSeed` | | |
| `demoSeed.enabled` | `false` | Consumed by R-10. |

## Secrets modes

The chart never renders a `kind: Secret`, and no value file carries a secret.
There are two modes (`secrets.mode`).

**`external-secrets`** (default). The chart renders ExternalSecrets against
`secrets.externalSecrets.storeRef`, with the Terraform `secrets` module's
output as remote keys:

| Terraform key (`secrets.arns.*`) | Becomes |
|---|---|
| `db_stac_higher_owner` | `<fullname>-db-owner` (`username`, `password`) |
| `db_stac_api`, `db_stac_reader`, `db_stac_proxy_policy`, `db_cube_server` | hook-scoped copies `<fullname>-hook-db-stac-api`, `-hook-db-stac-reader`, `-hook-db-proxy-policy`, `-hook-db-cube-server` |
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

## Install paths

Helm, with the digests from a release manifest (`docs/releases.md`, "Feeding
the chart"):

```sh
jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml

helm upgrade --install stac-higher oci://ghcr.io/<owner>/charts/stac-higher \
  --version <v> -n stac-higher --create-namespace \
  -f values.yaml -f digests.values.yaml --timeout 15m
```

Argo CD. The chart's `helm.sh/hook` annotations are mapped to Argo hooks
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
  source:
    repoURL: ghcr.io/<owner>/charts
    chart: stac-higher
    targetRevision: <v>
    helm:
      valueFiles: [values.yaml, digests.values.yaml]
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
cover every image the chart references.

The mirror rewrite applies to the images the chart pulls: `ghcr.io` becomes
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

- R-7: the catalog plane (stac-api, titiler, tipg, cube-server), their Services and routes.
- R-8: the process-run namespace, RBAC, Kueue and NodePool objects, and the runs NetworkPolicy.
- R-10: the demo seed Job and the demo destination.
