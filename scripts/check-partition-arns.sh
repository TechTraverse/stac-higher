#!/usr/bin/env bash
# Fail if any Terraform file hardcodes the AWS partition or DNS suffix.
# Usage: check-partition-arns.sh [dir...]
# No args: scan whichever of infra/terraform and infra/examples exist.
# Exit: 0 clean, 1 literal found, 2 bad usage / grep error.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dirs=()
if [ "$#" -gt 0 ]; then
  for d in "$@"; do
    [ -d "$d" ] || { echo "no such directory: $d" >&2; exit 2; }
    dirs+=("$d")
  done
else
  for d in "$root/infra/terraform" "$root/infra/examples"; do
    [ -d "$d" ] && dirs+=("$d")
  done
fi
[ "${#dirs[@]}" -gt 0 ] || exit 0

rc=0
grep -rnE --include='*.tf' --include='*.tfvars' --include='*.hcl' \
  --exclude-dir=.terraform -e 'arn:aws:' -e 'amazonaws\.com' "${dirs[@]}" || rc=$?
case "$rc" in
  0) echo "hardcoded partition/DNS-suffix literal found; use data.aws_partition.current" >&2; exit 1 ;;
  1) exit 0 ;;
  *) exit 2 ;;
esac
