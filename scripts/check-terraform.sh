#!/usr/bin/env bash
# Init, validate and test every module and example with one engine.
# Usage: check-terraform.sh <tofu|terraform>
set -euo pipefail

engine="${1:-}"
case "$engine" in
  tofu | terraform) ;;
  *) echo "usage: $0 <tofu|terraform>" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."
shopt -s nullglob
for dir in infra/terraform/modules/*/ infra/examples/*/; do
  echo "== $dir"
  "$engine" -chdir="$dir" init -backend=false -input=false
  "$engine" -chdir="$dir" validate
  if [ -d "$dir/tests" ]; then
    "$engine" -chdir="$dir" test
  fi
done
