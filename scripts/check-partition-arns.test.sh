#!/usr/bin/env bash
# Self-test for check-partition-arns.sh. Planted literals are assembled at
# runtime so this file never contains them itself.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
checker="$here/check-partition-arns.sh"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir "$work/clean" "$work/bad-arn" "$work/bad-host"
# shellcheck disable=SC2016
printf '%s\n' 'x = "arn:${data.aws_partition.current.partition}:iam::000000000000:root"' >"$work/clean/ok.tf"
printf 'x = "arn:%s:iam::000000000000:root"\n' aws >"$work/bad-arn/bad.tf"
printf 'x = "s3.%s.com"\n' amazonaws >"$work/bad-host/bad.tftest.hcl"

fail() { echo "FAIL: $1"; exit 1; }

rc=0
"$checker" "$work/clean" >/dev/null || rc=$?
[ "$rc" -eq 0 ] || fail "clean dir must exit 0 (got $rc)"

rc=0
out="$("$checker" "$work/bad-arn" 2>&1)" || rc=$?
[ "$rc" -eq 1 ] || fail "arn literal must exit 1 (got $rc)"
case "$out" in *bad.tf:1*) ;; *) fail "arn literal output must name bad.tf:1";; esac

rc=0
"$checker" "$work/bad-host" >/dev/null 2>&1 || rc=$?
[ "$rc" -eq 1 ] || fail "hostname literal in .tftest.hcl must exit 1 (got $rc)"

echo "partition check self-test: 3/3 passed"
