# Releases

What a `v*` tag publishes and the contract a deployment repo and the Helm
chart read. Spec §5 and §11 of
[the deployment artifacts design](superpowers/specs/2026-11-02-deployment-artifacts-design.md); ADR
[0024](decisions/0024-deployment-artifacts.md) for why
this repo publishes artifacts and a private repo deploys them.

## What a `v*` tag publishes

- **Images** to GHCR, one per row of the table below, tagged `X.Y.Z`, `X.Y`
  and `sha-<long sha>` (`containers.yml`). The `X.Y` tag is not published for
  pre-release tags: `v1.2.3-rc.1` publishes `1.2.3-rc.1` and `sha-…` only.
- **A GitHub release** (`release.yml`) carrying the app dist tarball, the
  pipeline wheel and sdist, and `release-manifest.json` (attached by the
  `release-attach` job in `containers.yml`).
- **The Helm chart**, at `oci://ghcr.io/<owner>/charts/stac-higher`
  (`release.yml`, jobs `chart` and `chart-push`). Its `version` and
  `appVersion` are both the tag without `v`, which is also the image tag, so
  the chart without `global.imageDigests` pulls the same release's images.
  See "The chart package".
- **The Terraform modules**: the tag itself is the version; the manifest's
  `terraformModules.ref` is a ready `source` string.

## Published images

Repository is `ghcr.io/<owner>/<repo>-<key>` (lowercased).

| Key | Built from | Platforms |
|---|---|---|
| `app` | `app/Dockerfile` | linux/amd64, linux/arm64 |
| `pipeline` | `services/pipeline/Dockerfile` | linux/amd64, linux/arm64 |
| `titiler` | `infra/titiler/Dockerfile` | linux/amd64 only (upstream base) |
| `cube-server` | `infra/cube-server/Dockerfile` | linux/amd64, linux/arm64 |
| `image-scanner` | `services/image-scanner/Dockerfile` | linux/amd64, linux/arm64 |
| `process-runtime` | `services/process-runtime/docker-bake.hcl`, target `runtime` | linux/amd64, linux/arm64 |
| `process-runtime-stactools` | `services/process-runtime/docker-bake.hcl`, target `stactools` | linux/amd64, linux/arm64 |
| `auth-proxy` | `infra/proxy-policy/Dockerfile` | linux/amd64, linux/arm64 |

Pull requests and non-`main` branches build and verify only; `main` and `v*`
push.

## Attestations

Images built from a `v*` tag carry SLSA provenance (`mode=max`) and an SPDX
SBOM as in-index attestation manifests. Inspect them with:

```sh
docker buildx imagetools inspect <ref> --format '{{json .Provenance}}'
docker buildx imagetools inspect <ref> --format '{{json .SBOM}}'
```

`main` images carry the build action's default provenance and no SBOM; `v*`
images carry explicit `mode=max` provenance and an SPDX SBOM. Images are not
signed (cosign: spec §12).

## Release manifest contract

`release-manifest.json`:

```json
{
  "schemaVersion": 1,
  "version": "v1.2.3",
  "gitSha": "<40 hex>",
  "images": {
    "app": {
      "ref": "ghcr.io/<owner>/<repo>-app:1.2.3",
      "digest": "sha256:<64 hex>"
    }
  },
  "chart": {
    "ref": "oci://ghcr.io/<owner>/charts/stac-higher",
    "version": "1.2.3"
  },
  "terraformModules": {
    "ref": "git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=v1.2.3"
  }
}
```

`images` holds all eight keys from the table (one shown here).

- `digest` is the pushed image's top-level (index) digest. Pull by
  `<ref without tag>@<digest>`; the tag in `ref` is informational and
  mutable.
- `chart.version` is the tag without the leading `v`.
- `terraformModules.ref` is usable as a module `source` as is.

Stability: keys are never renamed or removed. Adding an image adds a key and
keeps `schemaVersion: 1`. Changing a field's meaning bumps `schemaVersion`.

Dry runs: every Containers run uploads a `release-manifest` workflow artifact
versioned `v0.0.0-dryrun.<run number>`. On builds that did not push, the
digests come from the unpushed local build and are not pullable.

## Feeding the chart

```sh
jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml
```

JSON is valid YAML, so the file is a values file as is. The keys are the
image keys verbatim, so templates read them with
`index .Values.global.imageDigests "cube-server"` (R-6, R-9).

## The chart package

`scripts/package-chart.sh <tag> <out-dir>` holds every packaging command, and
the `chart` job runs exactly that script. To run it locally:
`scripts/package-chart.sh v0.0.0-dryrun.1 "$(mktemp -d)"`. It:

1. refuses a tag that breaks the manifest's rule (`vX.Y.Z` or `vX.Y.Z-pre`,
   no `+build`), so a bad tag fails before anything is pushed;
2. builds subcharts from `Chart.lock` (`helm dependency build`, with any
   http(s) chart repository registered in a throwaway repository config). If
   `Chart.yaml` declares `dependencies:` without a `Chart.lock`, or with a lock
   out of sync, the script fails rather than re-resolve version ranges at
   release time. Commit `Chart.lock` with every dependency change
   (`helm dependency update infra/helm/stac-higher`);
3. runs `helm package` with `--version` and `--app-version` set to the tag
   without `v`;
4. renders the archive on its own with `tests/values/base.yaml`, and checks
   the stamped `version` and `appVersion`.

**Dry run.** Pull requests that touch `infra/helm/**`, the script or
`release.yml`, and manual runs (`workflow_dispatch`), package version
`0.0.0-dryrun.<run number>` and upload the archive as the `chart` workflow
artifact. They never log in or push.

**Push.** On a `v*` tag, `chart-push` logs in to GHCR with the workflow token
(only that job holds `packages: write`), runs `helm push`, then runs
`helm show chart` against the pushed version. The job summary records the
ref and the OCI digest.

The chart is not signed or attested, and its digest is not in the manifest
(I-164). Until the package is public, `helm registry login ghcr.io` with a
token that has `read:packages`. After that, pulls are anonymous.

## Consuming a release

A deployment repo pins **one value, the tag** (`vX.Y.Z`), and derives
everything else from that release's `release-manifest.json`. The manifest is a
public release asset:
`https://github.com/<owner>/<repo>/releases/download/vX.Y.Z/release-manifest.json`.

| Pin | Manifest field | Used as |
|---|---|---|
| Chart | `chart.ref`, `chart.version` | `helm upgrade --install … <ref> --version <version>`; Argo CD `targetRevision`, Flux `ref.tag` |
| Images | `images.*.digest` | `digests.values.yaml` ("Feeding the chart") |
| Terraform modules | `terraformModules.ref` | the composition module's `source` |

A module `source` must be a literal string, because Terraform and OpenTofu
allow no variables there. So the root module spells out the ref, and the
pipeline checks it against the manifest. A version bump that changes one
without the other then fails before apply:

```hcl
module "stac_higher" {
  source = "git::https://github.com/<owner>/<repo>.git//infra/terraform/modules/stac-higher?ref=vX.Y.Z"
  # ...
}
```

The order is Terraform first, then Helm. Terraform creates the AWS resources
and emits `helm_values_yaml` (`infra/terraform/README.md`). Helm installs with
three values files: that output, the deployment's own values and the digests.
The examples below put the root module in `terraform/` and the deployment's
values in `values.yaml`. Pin the third-party actions and images to versions
you have reviewed.

GitHub Actions (deployment repo):

```yaml
env:
  STAC_HIGHER_VERSION: vX.Y.Z           # the one pin
  STAC_HIGHER_REPO: <owner>/<repo>

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write                   # only if your AWS login uses OIDC
    steps:
      - uses: actions/checkout@<version>
      - uses: azure/setup-helm@<version>
      - name: Release manifest
        run: |
          curl -fsSL -o release-manifest.json \
            "https://github.com/${STAC_HIGHER_REPO}/releases/download/${STAC_HIGHER_VERSION}/release-manifest.json"
          # The module source must match the pin.
          grep -rqF "$(jq -r .terraformModules.ref release-manifest.json)" terraform/
          jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml
      # AWS credentials, then: tofu -chdir=terraform init/apply, and
      #   tofu -chdir=terraform output -raw helm_values_yaml > tf.values.yaml
      # Cluster credentials (e.g. aws eks update-kubeconfig), then:
      - name: Install the chart
        run: |
          helm upgrade --install stac-higher "$(jq -r .chart.ref release-manifest.json)" \
            --version "$(jq -r .chart.version release-manifest.json)" \
            -n stac-higher --create-namespace \
            -f tf.values.yaml -f values.yaml -f digests.values.yaml --timeout 15m
```

GitLab CI (deployment repo):

```yaml
variables:
  STAC_HIGHER_VERSION: vX.Y.Z           # the one pin
  STAC_HIGHER_REPO: <owner>/<repo>

stages: [manifest, infra, deploy]

manifest:
  stage: manifest
  image: <image with curl and jq>
  script:
    - curl -fsSL -o release-manifest.json "https://github.com/${STAC_HIGHER_REPO}/releases/download/${STAC_HIGHER_VERSION}/release-manifest.json"
    - grep -rqF "$(jq -r .terraformModules.ref release-manifest.json)" terraform/
    - jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml
  artifacts:
    paths: [release-manifest.json, digests.values.yaml]

infra:
  stage: infra
  image: <OpenTofu or Terraform image>
  script:
    - tofu -chdir=terraform init
    - tofu -chdir=terraform apply -auto-approve   # or a reviewed plan file
    - tofu -chdir=terraform output -raw helm_values_yaml > tf.values.yaml
  artifacts:
    paths: [tf.values.yaml]                         # ARNs and names, no secrets
    expire_in: 1 day

deploy:
  stage: deploy
  image: <image with helm 3.8+ or 4, jq and cluster credentials>
  script:
    - helm upgrade --install stac-higher "$(jq -r .chart.ref release-manifest.json)"
      --version "$(jq -r .chart.version release-manifest.json)"
      -n stac-higher --create-namespace
      -f tf.values.yaml -f values.yaml -f digests.values.yaml --timeout 15m
```

Argo CD and Flux take the same chart ref and version: see "Install paths" in
the [chart README](../infra/helm/stac-higher/README.md).

## Cutting a release (lead)

These steps belong to the lead. The workflows never create tags or change
package visibility.

1. **Preconditions.** The commit is on `main`, and CI, Containers and Helm are
   green on it. The tag is `vX.Y.Z` or `vX.Y.Z-pre`; anything else fails the
   manifest and chart jobs.
2. **Tag:**

   ```sh
   git fetch origin main
   git tag -a v0.1.0 -m "v0.1.0" origin/main
   git push origin v0.1.0
   ```

3. **Watch both workflows.** Containers runs the eight images with
   attestations, then `release-manifest` and `release-attach`. Release runs
   `release`, `chart` and `chart-push`. The GitHub release ends with the app
   tarball, the pipeline wheel and sdist, and `release-manifest.json`. The
   `chart-push` summary shows the chart ref and digest.
4. **First release only: make the chart public.** The first push creates the
   `charts/stac-higher` package as private. In the org's Packages, open
   `charts/stac-higher`, then Package settings, then Change visibility, and
   set it to Public to match the images. On the same page, check that this
   repository has write access under "Manage Actions access". A package the
   workflow created has it; later tags push through it.
5. **Anonymous pull check**, from a shell with no GHCR credentials:

   ```sh
   owner=<owner, lowercase>; v=0.1.0
   token=$(curl -fsS "https://ghcr.io/token?scope=repository:${owner}/charts/stac-higher:pull" | jq -r .token)
   curl -fsS -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $token" \
     -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
     "https://ghcr.io/v2/${owner}/charts/stac-higher/manifests/${v}"
   ```

   Expect `200`. While the package is private, the token request itself
   fails with 403. Then confirm `helm pull` from a clean container. A
   workstation's `helm` may use stored registry credentials, so it proves
   nothing:

   ```sh
   docker run --rm alpine/helm:<helm version> pull "oci://ghcr.io/${owner}/charts/stac-higher" --version "$v"
   ```

   Run the same token probe for any image package that is new in this release.
   The existing image packages are already public.
6. **The manifest points at the chart.** Download `release-manifest.json` from
   the release. Its `chart.ref` and `chart.version` must equal the
   `chart-push` summary, and the anonymous probe above must succeed for
   that ref.
