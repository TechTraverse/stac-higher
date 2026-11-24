#!/usr/bin/env bash
# Lint and render the stac-higher chart under every values scenario.
# Usage: scripts/check-helm.sh <out-dir>   (run from the repo root)
set -euo pipefail

out=${1:?usage: scripts/check-helm.sh <out-dir>}
chart=infra/helm/stac-higher
vals=$chart/tests/values
kube=1.34.0
mkdir -p "$out"

run() {
  local name=$1
  shift
  local args=()
  local f
  for f in "$@"; do args+=(-f "$f"); done
  echo "== $name"
  helm lint --strict --namespace stac-higher --kube-version "$kube" "$chart" "${args[@]}" \
    || { echo "FAILED: helm lint, scenario $name" >&2; exit 1; }
  helm template stac-higher "$chart" --namespace stac-higher --kube-version "$kube" "${args[@]}" \
    > "$out/$name.yaml" \
    || { echo "FAILED: helm template, scenario $name" >&2; exit 1; }
}

run base "$vals/base.yaml"
run existing-secrets "$vals/base.yaml" "$vals/existing-secrets.yaml"
run irsa "$vals/base.yaml" "$vals/irsa.yaml"
run digests-mirror "$vals/base.yaml" "$vals/digests-mirror.yaml"
run existing-irsa "$vals/base.yaml" "$vals/existing-secrets.yaml" "$vals/irsa.yaml"
run r5 "$vals/r5-helm-values.yaml" "$vals/r5-deployment.yaml"
run example infra/examples/aws-eks/values.yaml
