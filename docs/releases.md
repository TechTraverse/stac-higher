# Releases

What a `v*` tag publishes and the contract a deployment repo and the Helm
chart read. Spec §5 and §11 (Phase 8 deployment artifacts); ADR
[0024](decisions/0024-deployment-artifacts.md) for why
this repo publishes artifacts and a private repo deploys them.

## What a `v*` tag publishes

- **Images** to GHCR, one per row of the table below, tagged `X.Y.Z`, `X.Y`
  and `sha-<long sha>` (`containers.yml`).
- **A GitHub release** (`release.yml`) carrying the app dist tarball, the
  pipeline wheel and sdist, and `release-manifest.json` (attached by the
  `release-manifest` job in `containers.yml`).
- **The Helm chart** to `oci://ghcr.io/<owner>/charts/stac-higher` (R-9, not
  yet published).
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

`main` images carry only the default minimal provenance. Nothing is signed
yet (cosign is deferred, spec §12).

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

- `digest` is the multi-arch **index** digest. Pull by
  `<ref without tag>@<digest>`; the tag in `ref` is informational and
  mutable.
- `chart.version` is the tag without the leading `v`.
- `terraformModules.ref` is usable as a module `source` as is.

Stability: keys are never renamed or removed. Adding an image adds a key and
keeps `schemaVersion: 1`. Changing a field's meaning bumps `schemaVersion`.

Dry runs: every Containers run uploads a `release-manifest` workflow artifact
versioned `v0.0.0-dryrun.<run number>`. On builds that did not push, the
digests are local image ids and are not pullable.

## Feeding the chart

```sh
jq '{global: {imageDigests: (.images | map_values(.digest))}}' release-manifest.json > digests.values.yaml
```

JSON is valid YAML, so the file is a values file as is. The keys are the
image keys verbatim, so templates read them with
`index .Values.global.imageDigests "cube-server"` (R-6, R-9).
