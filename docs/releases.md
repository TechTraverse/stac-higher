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
  (`containers.yml`, jobs `chart` and `chart-push`). Its `version` and
  `appVersion` are both the tag without `v`, which is also the image tag, so
  the chart without `global.imageDigests` pulls the same release's images.
  See "The chart package".

Order within `containers.yml` (spec §11): `version` checks the tag first;
the images build and push; `chart-push` runs only when every image and the
chart package succeeded; `release-manifest` runs only after `chart-push` and
records the chart digest; `release-attach` adds the manifest to the GitHub
release. `release.yml` builds the GitHub release and its app and pipeline
assets in parallel, independent of the rest.
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
    "version": "1.2.3",
    "digest": "sha256:<64 hex>"
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
- `chart.digest` is the pushed chart's OCI manifest digest. Every release
  manifest has it, because the manifest is built only after the chart push
  succeeds. Dry runs push nothing and omit it.
- `terraformModules.ref` is usable as a module `source` as is.
- `version` follows SemVer 2.0 with a `v` prefix and no `+build` metadata.
  Pre-release identifiers are non-empty, and numeric ones have no leading
  zero, so `v1.2.3-rc.1` is valid while `v1.2.3-rc..1` and `v1.2.3-01` are
  not. Every version this rule accepts is one Helm accepts too.

Stability: keys are never renamed or removed. Adding an image adds a key and
keeps `schemaVersion: 1`; so did adding `chart.digest`. Changing a field's
meaning bumps `schemaVersion`.

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

1. refuses a tag that breaks the manifest's version rule ("Release manifest
   contract"). The bash copy of the rule and `VERSION_RE` are kept identical
   by `app/src/__tests__/release-manifest.test.ts`.
   `scripts/package-chart.sh --check-tag <tag>` runs only this step: the
   `version` job uses it, so a bad tag fails before any image builds;
2. builds subcharts from `Chart.lock` (`helm dependency build`, with any
   http(s) chart repository registered in a throwaway repository config). It
   reads the dependencies and their repositories through `helm show chart`,
   so block and flow style both work and `dependencies: []` counts as none.
   If `Chart.yaml` declares dependencies without a `Chart.lock`, or with a
   lock out of sync, the script fails rather than re-resolve version ranges
   at release time. Commit `Chart.lock` with every dependency change
   (`helm dependency update infra/helm/stac-higher`). The fetched
   `charts/*.tgz` are gitignored;
3. runs `helm package` with `--version` and `--app-version` set to the tag
   without `v`;
4. renders the archive on its own with `tests/values/base.yaml`, and checks
   the stamped `version` and `appVersion`.

**Dry run.** Every Containers run that is not a `v*` tag push runs the
`chart` job: pull requests, `main` and `ci/**` pushes, and manual runs
(`workflow_dispatch`). It packages version `0.0.0-dryrun.<run number>` and
uploads the archive as the `chart` workflow artifact. Nothing on that path
holds a write-scoped chart job: `chart` is `contents: read`, and
`chart-push` runs only on a `v*` tag push.

**Push.** On a `v*` tag, `chart-push` waits for every image leg and the
package. It logs in to GHCR with the workflow token (`packages: write`), runs
`helm push`, takes the digest Helm reports, and runs `helm show chart`
against the pushed version. The digest goes to the job summary and to
`release-manifest` as `chart.digest`.

The chart is not signed or attested (I-164). Until the package is public,
`helm registry login ghcr.io` with a token that has `read:packages`. After
that, pulls are anonymous.

## Consuming a release

A deployment repo pins **one value, the tag** (`vX.Y.Z`), and derives
everything else from that release's `release-manifest.json`. The manifest is a
public release asset:
`https://github.com/<owner>/<repo>/releases/download/vX.Y.Z/release-manifest.json`.

| Pin | Manifest field | Used as |
|---|---|---|
| Chart | `chart.ref`, `chart.version` | `helm upgrade --install … <ref> --version <version>`; Argo CD `targetRevision`, Flux `ref.tag` |
| Chart digest | `chart.digest` | the check below; Flux `ref.digest` |
| Images | `images.*.digest` | `digests.values.yaml` ("Feeding the chart") |
| Terraform modules | `terraformModules.ref` | the composition module's `source` |

A chart version tag can be pushed again: re-running a tag's Containers
workflow overwrites it (I-164). A deployment that needs immutability records
`chart.digest` and checks, before installing, that the version still resolves
to it. The check needs no credentials once the package is public:

```sh
repo=${CHART_REF#oci://ghcr.io/}    # <owner>/charts/stac-higher
token=$(curl -fsS "https://ghcr.io/token?scope=repository:${repo}:pull" | jq -r .token)
live=$(curl -fsSI -H "Authorization: Bearer $token" \
  -H 'Accept: application/vnd.oci.image.manifest.v1+json' \
  "https://ghcr.io/v2/${repo}/manifests/${CHART_VERSION}" \
  | tr -d '\r' | awk 'tolower($1) == "docker-content-digest:" { print $2 }')
[ "$live" = "$(jq -r .chart.digest release-manifest.json)" ] || { echo "chart ${CHART_VERSION} moved"; exit 1; }
```

Here `CHART_REF` and `CHART_VERSION` are `chart.ref` and `chart.version`.
Flux can pin the digest itself (`OCIRepository` `spec.ref.digest`).

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
    - |
      curl -fsSL -o release-manifest.json \
        "https://github.com/${STAC_HIGHER_REPO}/releases/download/${STAC_HIGHER_VERSION}/release-manifest.json"
      # The module source must match the pin.
      grep -rqF "$(jq -r .terraformModules.ref release-manifest.json)" terraform/
      jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml
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
    - |
      helm upgrade --install stac-higher "$(jq -r .chart.ref release-manifest.json)" \
        --version "$(jq -r .chart.version release-manifest.json)" \
        -n stac-higher --create-namespace \
        -f tf.values.yaml -f values.yaml -f digests.values.yaml --timeout 15m
```

Argo CD and Flux take the same chart ref and version: see "Install paths" in
the [chart README](../infra/helm/stac-higher/README.md).

## Cutting a release (lead)

These steps belong to the lead. The workflows never create tags or change
package visibility.

1. **Preconditions.** The commit is on `main`, and CI, Containers and Helm are
   green on it. Check the tag first with
   `scripts/package-chart.sh --check-tag v0.1.0`. A tag that breaks the rule
   ("Release manifest contract") fails the Containers `version` job before
   any image builds, so nothing is published. `release.yml` does not check
   the tag: it still creates a GitHub release with the app and pipeline
   assets, which you then delete (see "When a release half-fails").
2. **Tag:**

   ```sh
   git fetch origin main
   git tag -a v0.1.0 -m "v0.1.0" origin/main
   git push origin v0.1.0
   ```

3. **Watch both workflows.** Containers runs, in order:
   - `version`;
   - the eight images with attestations, alongside `chart`;
   - `chart-push`;
   - `release-manifest`;
   - `release-attach`.

   Release runs `release`. The GitHub release ends with the app tarball, the
   pipeline wheel and sdist, and `release-manifest.json`. The `chart-push`
   summary shows the chart ref and digest.
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
   the release. Its `chart.ref`, `chart.version` and `chart.digest` must equal
   the `chart-push` summary, and the digest check in "Consuming a release"
   must pass anonymously.

### When a release half-fails

The ordering means a failure leaves one of these states. Find yours in the
Containers run.

| Failed job | What exists | What does not |
|---|---|---|
| `version` | the GitHub release from `release.yml` (app and pipeline assets) | images, chart, manifest |
| an image leg, or `chart` | the images that finished pushing (tags `X.Y.Z`, `X.Y`, `sha-…`), and the GitHub release | the chart, the manifest |
| `chart-push` | every image, the GitHub release, and maybe the chart version if the push landed before a later step failed | the manifest |
| `release-manifest` / `release-attach` | images, chart, GitHub release | `release-manifest.json` on the release |
| `release` (in `release.yml`) | everything from Containers, maybe the manifest on a release that `release-attach` created | the app and pipeline assets |

Nothing a deployment consumes is published until `release-manifest.json` is
on the release, because a deployment pins from the manifest. A half-failed
release is therefore safe to repair in place.

- **Transient failure** (registry timeout, runner loss): re-run the failed
  jobs of that workflow run ("Re-run failed jobs"). Image, chart and manifest
  re-pushes for the same tag overwrite, so this is idempotent.
- **A defect in the commit** (a broken build, a chart that does not package):
  do not move the tag. Burn it and cut the next patch version from a fixed
  `main`. Delete what the burned tag left behind:
  - the GitHub release: `gh release delete vX.Y.Z --yes` (keep the git tag,
    so the version is never reused);
  - the chart version, if `chart-push` got that far: in the org's Packages,
    open `charts/stac-higher`, then its version `X.Y.Z`, then "Delete
    version". Or, with a token that has `delete:packages`:
    `gh api -X DELETE /orgs/<owner>/packages/container/charts%2Fstac-higher/versions/<version id>`.
    The id comes from
    `gh api /orgs/<owner>/packages/container/charts%2Fstac-higher/versions`;
  - image versions are optional to delete. Nothing references them without a
    manifest. Delete them the same way under `<repo>-<key>` to keep the
    package lists clean.
- **A bad tag name** fails at `version` and publishes nothing from
  Containers. Delete the GitHub release that `release.yml` made, then delete
  the tag (`git push origin :refs/tags/<tag>`).
