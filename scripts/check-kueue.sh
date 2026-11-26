#!/usr/bin/env bash
# Validate infra/kueue and infra/kubernetes against Kueue's OWN CRD schemas
# (K-6, plan K6-R15). Usage: scripts/check-kueue.sh [work-dir]  (repo root)
#
# Downloads the pinned Kueue manifests.yaml (sha256-checked against
# infra/kueue/kueue.env), turns its CRDs into JSON schemas with kubeconform's
# openapi2jsonschema.py (pinned, sha256-checked), deletes the v1beta1 schemas
# (Kueue v0.20 no longer serves v1beta1, so a v1beta1 manifest must fail), and
# runs kubeconform -strict. Built-in kinds (NetworkPolicy, the admission
# policy, RBAC) validate against Kubernetes 1.34's own schemas. CEL inside the
# admission policy cannot be checked statically: the kind leg is its test.
#
# Needs: curl, uv, and kubeconform v0.8.0 on PATH (CI installs it as
# .github/workflows/helm.yml does; locally download kubeconform-<os>-<arch>
# v0.8.0 from github.com/yannh/kubeconform/releases, check its sha256 --
# darwin-arm64: f84f4dfbebf4a6b0b230385fa065a39ea35e02608c2b50d025dcf64775a69d67
# -- and put the binary on PATH).
set -euo pipefail

work=${1:-$(mktemp -d)}
O2J_URL=https://raw.githubusercontent.com/yannh/kubeconform/v0.8.0/scripts/openapi2jsonschema.py
O2J_SHA256=d145babfbb765004030764e1b4e518bfb7a4bd7f111691a08fa57983b81881f3
KUBE_VERSION=1.34.0

if ! command -v kubeconform >/dev/null 2>&1; then
  echo "check-kueue: kubeconform v0.8.0 is not on PATH (see this script's header)" >&2
  exit 2
fi

sha256_check() { # <sha256> <file>
  if command -v sha256sum >/dev/null 2>&1; then
    echo "$1  $2" | sha256sum -c -
  else
    echo "$1  $2" | shasum -a 256 -c -
  fi
}

# shellcheck source=../infra/kueue/kueue.env
source infra/kueue/kueue.env
mkdir -p "$work"
curl -sSfL -o "$work/manifests.yaml" \
  "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"
sha256_check "$KUEUE_MANIFESTS_SHA256" "$work/manifests.yaml"
curl -sSfL -o "$work/openapi2jsonschema.py" "$O2J_URL"
sha256_check "$O2J_SHA256" "$work/openapi2jsonschema.py"

rm -rf "$work/schemas"
mkdir "$work/schemas"
(cd "$work/schemas" && uv run --no-project --with pyyaml python -I ../openapi2jsonschema.py ../manifests.yaml >/dev/null)
rm -f "$work"/schemas/*_v1beta1.json
if ! ls "$work"/schemas/clusterqueue_v1beta2.json >/dev/null 2>&1; then
  echo "check-kueue: no ClusterQueue v1beta2 schema was generated" >&2
  exit 1
fi

kubeconform -strict -summary -kubernetes-version "$KUBE_VERSION" \
  -schema-location default \
  -schema-location "$work/schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
  infra/kueue/common infra/kueue/kind infra/kubernetes
