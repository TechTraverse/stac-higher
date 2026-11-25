#!/usr/bin/env bash
# Package the stac-higher chart for a release tag (R-9; docs/releases.md).
# Usage: scripts/package-chart.sh <tag> <out-dir>   (run from the repo root)
#        scripts/package-chart.sh --check-tag <tag>  (the tag rule only)
#
# <tag> follows the release manifest's rule, VERSION_RE in
# .github/scripts/release-manifest.mjs: SemVer 2.0 with a v prefix, no
# +build, pre-release identifiers non-empty and numeric ones without a
# leading zero. Every such version is one Helm accepts, so a tag that passes
# here never fails `helm package`. A test keeps the two copies of the rule in
# step (app/src/__tests__/release-manifest.test.ts).
#
# The chart's version and appVersion both become the tag without its leading
# v. That is the manifest's chart.version and the image tag containers.yml
# publishes, so a chart installed without global.imageDigests pulls the same
# release's images.
#
# Subcharts come from Chart.lock, never re-resolved at release time.
# The archive is rendered once on its own to prove it is complete.
# Helm's output goes to stderr; the last stdout line is the archive path.
# CHART_DIR overrides the chart directory (tests only).
set -euo pipefail

usage="usage: scripts/package-chart.sh <tag> <out-dir> | --check-tag <tag>"
chart=${CHART_DIR:-infra/helm/stac-higher}
kube=1.34.0

fail() {
  echo "package-chart: $*" >&2
  exit 1
}

pre_id='(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
semver="^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-${pre_id}(\.${pre_id})*)?$"
check_tag() {
  [[ $1 =~ $semver ]] \
    || fail "tag must be SemVer 2.0 with a v prefix and no +build (v1.2.3, v1.2.3-rc.1), got '$1'"
}

if [[ ${1:-} == --check-tag ]]; then
  check_tag "${2:?$usage}"
  exit 0
fi

tag=${1:?$usage}
out=${2:?$usage}
check_tag "$tag"
version=${tag#v}

# `helm show chart` parses Chart.yaml (block or flow style) and prints it
# normalised, with an empty dependency list omitted.
meta=$(helm show chart "$chart") || fail "cannot read $chart/Chart.yaml"
if grep -q '^dependencies:' <<<"$meta"; then
  # Without a lock, `helm dependency build` falls back to `update` and
  # resolves version ranges afresh, so a release could ship a subchart nobody
  # reviewed.
  [[ -f $chart/Chart.lock ]] \
    || fail "$chart/Chart.yaml declares dependencies but $chart/Chart.lock is missing; run 'helm dependency update $chart' and commit Chart.lock"

  # With a lock, `helm dependency build` needs every http(s) chart repository
  # registered (oci:// and file:// need nothing). Register them in a
  # throwaway repository config, so a local run leaves the user's Helm
  # config alone.
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  export HELM_REPOSITORY_CONFIG="$work/repositories.yaml" HELM_REPOSITORY_CACHE="$work/repository"
  n=0
  while IFS= read -r url; do
    n=$((n + 1))
    helm repo add "stac-higher-dep-$n" "$url" >&2 || fail "helm repo add failed for $url"
  done < <(sed -n -E "s/^  repository: *['\"]?([^'\"]*)['\"]?[[:space:]]*$/\1/p" <<<"$meta" \
    | grep -E '^https?://' | sort -u || true)
fi
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

stamped=$(helm show chart "$archive")
grep -qx "version: $version" <<<"$stamped" || fail "$archive does not carry version $version"
grep -qx "appVersion: $version" <<<"$stamped" || fail "$archive does not carry appVersion $version"

echo "$archive"
