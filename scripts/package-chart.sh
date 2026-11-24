#!/usr/bin/env bash
# Package the stac-higher chart for a release tag (R-9; docs/releases.md).
# Usage: scripts/package-chart.sh <tag> <out-dir>   (run from the repo root)
#
# <tag> is vX.Y.Z or vX.Y.Z-pre: the release manifest's rule, with no +build
# (.github/scripts/release-manifest.mjs). The chart's version and appVersion
# both become the tag without its leading v. That is the manifest's
# chart.version and the image tag containers.yml publishes, so a chart
# installed without global.imageDigests pulls the same release's images.
#
# Subcharts come from Chart.lock, never re-resolved at release time.
# The archive is rendered once on its own to prove it is complete.
# Helm's output goes to stderr; the last stdout line is the archive path.
# CHART_DIR overrides the chart directory (tests only).
set -euo pipefail

usage="usage: scripts/package-chart.sh <tag> <out-dir>"
tag=${1:?$usage}
out=${2:?$usage}
chart=${CHART_DIR:-infra/helm/stac-higher}
kube=1.34.0

fail() {
  echo "package-chart: $*" >&2
  exit 1
}

semver='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$'
[[ $tag =~ $semver ]] || fail "tag must look like v1.2.3 or v1.2.3-rc.1 (no +build), got '$tag'"
version=${tag#v}

# Without a lock, `helm dependency build` falls back to `update` and resolves
# version ranges afresh, so a release could ship a subchart nobody reviewed.
if grep -Eq '^dependencies:[[:space:]]*(#.*)?$' "$chart/Chart.yaml" && [[ ! -f $chart/Chart.lock ]]; then
  fail "$chart/Chart.yaml declares dependencies but $chart/Chart.lock is missing; run 'helm dependency update $chart' and commit Chart.lock"
fi
# With a lock, `helm dependency build` needs every http(s) chart repository
# registered (oci:// and file:// need nothing). Register them in a throwaway
# repository config so a local run leaves the user's Helm config alone.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export HELM_REPOSITORY_CONFIG="$work/repositories.yaml" HELM_REPOSITORY_CACHE="$work/repository"
n=0
while IFS= read -r url; do
  n=$((n + 1))
  helm repo add "stac-higher-dep-$n" "$url" >&2 || fail "helm repo add failed for $url"
done < <(helm dependency list "$chart" 2>/dev/null \
  | awk -F'\t' 'NR > 1 { gsub(/ /, "", $3); if ($3 ~ /^https?:\/\//) print $3 }' | sort -u)
# No-op without dependencies; otherwise fetches exactly what Chart.lock pins
# and fails when the lock is out of sync with Chart.yaml.
helm dependency build "$chart" >&2 || fail "helm dependency build failed for $chart"

mkdir -p "$out"
helm package "$chart" --version "$version" --app-version "$version" --destination "$out" >&2 \
  || fail "helm package failed for $chart"
archive="$out/stac-higher-$version.tgz"
[[ -f $archive ]] || fail "expected $archive after helm package"

# tests/ is .helmignored, so the scenario's values come from the checkout.
helm template stac-higher "$archive" --namespace stac-higher --kube-version "$kube" \
  -f "$chart/tests/values/base.yaml" > /dev/null \
  || fail "the packaged chart $archive does not render with $chart/tests/values/base.yaml"

meta=$(helm show chart "$archive")
grep -qx "version: $version" <<<"$meta" || fail "$archive does not carry version $version"
grep -qx "appVersion: $version" <<<"$meta" || fail "$archive does not carry appVersion $version"

echo "$archive"
