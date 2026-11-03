# ADR 0024 — Deployment artifacts: this repo publishes, a private repo deploys; Terraform for AWS, Helm for Kubernetes, services on EKS

- **Status:** accepted (2026-11-03; proposed 2026-11-02) — the lead approved
  the design spec `docs/superpowers/specs/2026-11-02-deployment-artifacts-design.md`.
- **Supersedes:** ROADMAP Phase 8's "AWS stack via eoapi-cdk extended" and
  the §7 "eoapi-cdk … Adopt in Phase 8" row. eoapi-k8s stays adopted.
- **Settles:** the Phase 8 open question "where the app, pipeline and
  proxies run" (ROADMAP Phase 8, K spec §11.1): on EKS, beside the process
  runs, in separate namespaces.
- **Related:** ADR 0001 (migration ownership — why eoapi's pgstac
  installers are switched off), ADR 0011 (byte deletion — bucket versioning
  off), ADR 0015 (proxy write policy — the derived auth-proxy image and its
  read-only DB role), ADR 0019 (Kubernetes Jobs + Kueue), ADR 0022 (the
  cube-server); GitHub #16 (re-scoped to the GPU leg), #68 (egress lockdown).

## Context

Phase 8 was planned as "eoapi-cdk extended": a CDK stack adding our app,
pipeline and storage to eoapi's constructs. Three facts have changed since.

1. **Process compute needs Kubernetes** (ADR 0019): runs are Kubernetes Jobs
   admitted by Kueue on EKS. With a cluster already operated for runs, the
   team runs the app, pipeline and proxies there too rather than adding
   ECS/Fargate as a second orchestrator.
2. **eoapi-cdk's services are Lambda + API Gateway** (v11.8.1: stac-api,
   titiler-pgstac, tipg, stac-auth-proxy, ingestor). On EKS they do not
   apply. What remains is `PgStacDatabase`, an RDS instance plus a
   bootstrapper Lambda that installs pgstac — which competes with our own
   `pypgstac migrate` (ADR 0001).
3. **This repo is public and must carry no account details.** Deployments
   are made from private repos the team controls (GitLab or GitHub CI), and
   the EKS cluster is created by a separate cluster repo.

## Decision

1. **Publish, don't deploy.** This repo publishes versioned artifacts on
   each `v*` tag: container images (GHCR), a Helm chart (OCI on GHCR),
   Terraform/OpenTofu modules (consumed by git tag) and a release manifest
   of image digests. It contains no account ids, ARNs, hostnames, CIDRs or
   secrets, and no deployment pipeline. A private deployment repo supplies
   the values and deploys.
2. **Terraform/OpenTofu for AWS resources** the platform needs beyond the
   cluster: RDS, S3, KMS, IAM/workload identity, Secrets Manager, ECR
   pull-through cache, optional Cognito. Modules never configure a provider,
   backend or account; they work with OpenTofu ≥ 1.10 and Terraform ≥ 1.10.
3. **Helm for Kubernetes resources.** One `stac-higher` chart with
   eoapi-k8s as a subchart for the catalog plane (stac-fastapi,
   titiler-pgstac, tipg, stac-auth-proxy), its pgstac bootstrap and bundled
   Postgres operator disabled. Terraform does not create Kubernetes objects.
4. **eoapi-cdk is not adopted.** RDS is built in Terraform, using
   eoapi-cdk's pgstac parameter tuning as a reference.
5. **The EKS cluster is out of this repo.** The chart documents what the
   cluster must provide (EKS 1.34+ Auto Mode, NetworkPolicy enforcement,
   Kueue controller, External Secrets or pre-created Secrets, an
   IngressClass). The app-specific cluster-scoped objects (Kueue queues and
   flavors, the runs NodePool, the admission policy) ship in the chart
   behind one toggle, because their numbers must move together.
6. **GovCloud-portable where cheap:** partition-aware ARNs, no ECR Public or
   commercial-only services, Pod Identity with an IRSA toggle.

## Consequences

- One IaC engine for AWS state; Kubernetes state is Helm's. The Terraform →
  Helm handoff is a `helm_values` output, so no ARN is copied by hand.
- Every artifact is validated in this repo's CI by rendering (validate,
  lint, template, schema checks), never by applying. The first real apply is
  a lead-only rehearsal from a deployment repo.
- The app must take every browser-visible URL at runtime, not build time,
  so one image serves any deployment (a code change; spec §8).
- Workloads authenticate to AWS only through workload identity; the
  platform's static-key defaults become fallbacks for local compose only.
- Because the pipeline itself runs on an IAM role, its `AssumeRole` into the
  process-run role is role chaining, capped at one hour; process runs on
  AWS are bounded by that until a later design lifts it.
- eoapi-k8s becomes a dependency we track. If it cannot take our images,
  external database and disabled bootstrap, those four services fall back
  to our own templates without changing this decision.
- #16 (K-8) keeps only the GPU leg; the cluster leaves this repo's scope.

## Alternatives considered

- **eoapi-cdk extended (the original plan).** Rejected: Lambda-based
  services do not fit EKS, and using it for RDS alone means two IaC engines
  and two pgstac installers.
- **Terraform for everything, including Kubernetes objects.** Rejected:
  CRD-backed objects (Kueue, ExternalSecret, NodePool) cannot be planned in
  the same apply that installs their CRDs, and Helm is the team's usual
  delivery format.
- **This repo deploys (environment folders with tfvars here).** Rejected:
  the repo is public and the team does not want account details in it.
- **The cluster in this repo.** Rejected: the cluster outlives this app,
  may host other workloads, and changes on a different cadence.
- **ECS/Fargate for the services.** Rejected: a second orchestrator beside
  the EKS cluster process runs already need.
