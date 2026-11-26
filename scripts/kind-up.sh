#!/usr/bin/env bash
# Bring up the Kubernetes executor's kind cluster (K-6, plan K6-R2..R6, R19,
# R25). Usage: scripts/kind-up.sh <out-dir>   (run from the repo root)
#
# Creates kind cluster `stac-higher` (pinned node image), installs the pinned
# Kueue release by server-side apply with infra/kueue/config/kind.yaml swapped
# in, applies K-5's infra/kubernetes/ (namespace, run ServiceAccount, the
# pipeline's Role and binding, the default-deny NetworkPolicy) and
# infra/kueue/{common,kind}, waits until the queues are Active and the
# admission policies enforce, and mints a 2 h token for the pipeline's
# ServiceAccount. Writes into <out-dir>:
#   admin.kubeconfig   kind's admin credentials (the test HARNESS only)
#   pipeline.token     the pipeline ServiceAccount's bound token (the EXECUTOR)
#   ca.crt             the cluster CA
#   kind.env           what the kind test modules read (`set -a; . kind.env`)
# It does not build or load the run image (the workflow overlaps that with
# cluster creation); it prints the commands a local user runs for it.
# Needs: docker, kind, kubectl, curl, uv, sha256sum or shasum. Never prints
# the token.
set -euo pipefail

out=${1:?usage: scripts/kind-up.sh <out-dir>}
mkdir -p "$out"
out=$(cd "$out" && pwd)
kc="$out/admin.kubeconfig"
RUNS_NS=stac-higher-runs
PIPELINE_NS=stac-higher
PIPELINE_SA=stac-higher-pipeline
CLUSTER=stac-higher
RUNTIME_IMAGE=stac-higher-process-runtime:kind

# shellcheck source=../infra/kind/versions.env
source infra/kind/versions.env
# shellcheck source=../infra/kueue/kueue.env
source infra/kueue/kueue.env

k() { kubectl --kubeconfig "$kc" "$@"; }
step() { echo "== kind-up: $*"; }
die() { echo "kind-up: FAILED: $*" >&2; exit 1; }
sha256_check() { # <sha256> <file>
  if command -v sha256sum >/dev/null 2>&1; then
    echo "$1  $2" | sha256sum -c -
  else
    echo "$1  $2" | shasum -a 256 -c -
  fi
}

step "kind cluster $CLUSTER ($KIND_NODE_IMAGE)"
kind create cluster --config infra/kind/cluster.yaml --image "$KIND_NODE_IMAGE" \
  --kubeconfig "$kc" --wait 180s

step "Kueue $KUEUE_VERSION (K6-R6)"
curl -sSfL -o "$out/kueue-manifests.yaml" \
  "https://github.com/kubernetes-sigs/kueue/releases/download/${KUEUE_VERSION}/manifests.yaml"
sha256_check "$KUEUE_MANIFESTS_SHA256" "$out/kueue-manifests.yaml" \
  || die "Kueue manifests.yaml does not match KUEUE_MANIFESTS_SHA256 (infra/kueue/kueue.env)"
# K-6 review R3 (docs/ISSUES.md I-186): Kueue's webhooks match only the runs
# namespace, so nothing elsewhere depends on Kueue being up.
uv run --no-project --with pyyaml python -I scripts/kueue-narrow-webhooks.py \
  "$out/kueue-manifests.yaml" "$out/kueue-manifests-narrowed.yaml" \
  --namespace "$RUNS_NS" --expect infra/kueue/webhooks.txt
k apply --server-side -f "$out/kueue-manifests-narrowed.yaml" >/dev/null
k -n kueue-system create configmap kueue-manager-config \
  --from-file=controller_manager_config.yaml=infra/kueue/config/kind.yaml \
  --dry-run=client -o yaml \
  | k apply --server-side --force-conflicts --field-manager=stac-higher -f -
k -n kueue-system rollout restart deploy/kueue-controller-manager
k -n kueue-system rollout status deploy/kueue-controller-manager --timeout=300s

step "the pipeline's identity and K-5's runs namespace objects (infra/kubernetes)"
k create namespace "$PIPELINE_NS"
k -n "$PIPELINE_NS" create serviceaccount "$PIPELINE_SA"
# The namespace first: kubectl applies a directory file by file.
k apply --server-side -f infra/kubernetes/namespace.yaml
k apply --server-side -f infra/kubernetes/

step "Kueue objects and the admission policies (infra/kueue/common, infra/kueue/kind)"
# Kueue's webhooks answer a little after its Deployment reports available.
applied=false
for _ in $(seq 1 12); do
  if k apply --server-side -f infra/kueue/common/ -f infra/kueue/kind/; then
    applied=true
    break
  fi
  sleep 5
done
$applied || die "the Kueue objects were not accepted after 12 tries (is Kueue's webhook up?)"
k wait --for=condition=Active clusterqueue/process-runs --timeout=120s
k -n "$RUNS_NS" wait --for=condition=Active localqueue/process-runs --timeout=120s

step "admission policies: type-checked and enforcing (K6-R25)"
for policy in stac-higher-runs-jobs stac-higher-runs-pod-updates; do
  checked=false
  for _ in $(seq 1 30); do
    gen=$(k get validatingadmissionpolicy "$policy" -o jsonpath='{.metadata.generation}')
    seen=$(k get validatingadmissionpolicy "$policy" -o jsonpath='{.status.observedGeneration}')
    if [ -n "$seen" ] && [ "$seen" = "$gen" ]; then checked=true; break; fi
    sleep 2
  done
  $checked || die "ValidatingAdmissionPolicy $policy was never type-checked (status.observedGeneration)"
  warnings=$(k get validatingadmissionpolicy "$policy" \
    -o jsonpath='{.status.typeChecking.expressionWarnings}')
  if [ -n "$warnings" ]; then
    echo "$warnings" >&2
    die "ValidatingAdmissionPolicy $policy has CEL type-checking warnings (above)"
  fi
done
probe_job() {
  cat <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: k6-policy-probe
  namespace: $RUNS_NS
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: probe
          image: $RUNTIME_IMAGE
EOF
}
enforcing=false
for _ in $(seq 1 30); do
  if result=$(probe_job | k create --dry-run=server -f - 2>&1); then
    : # admitted: the policy is not active yet
  elif grep -q 'stac-higher-runs-jobs' <<<"$result"; then
    enforcing=true
    break
  else
    echo "$result" >&2
  fi
  sleep 2
done
$enforcing || die "the runs namespace still admits a Job without a queue label after 60 s"

step "credentials for the tests (the executor runs as $PIPELINE_SA)"
umask 077
k -n "$PIPELINE_NS" create token "$PIPELINE_SA" --duration=2h >"$out/pipeline.token"
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  echo "::add-mask::$(cat "$out/pipeline.token")"
fi
k config view --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}' \
  | base64 -d >"$out/ca.crt"
url=$(k config view -o jsonpath='{.clusters[0].cluster.server}')
[ "$url" = "https://127.0.0.1:6443" ] || echo "kind-up: note: the API server is at $url" >&2

cat >"$out/kind.env" <<EOF
KIND_INTEGRATION=1
PROCESS_EXECUTOR=kubernetes
PROCESS_K8S_API_URL=$url
PROCESS_K8S_TOKEN_FILE=$out/pipeline.token
PROCESS_K8S_CA_FILE=$out/ca.crt
KIND_ADMIN_KUBECONFIG=$kc
KIND_RUNTIME_IMAGE=$RUNTIME_IMAGE
KIND_OUT=$out
EOF

echo "kind-up: kind $KIND_VERSION, node $KIND_NODE_IMAGE, Kueue $KUEUE_VERSION, API $url"
echo "kind-up: the run image is not loaded by this script. Locally:"
echo "  docker buildx bake -f services/process-runtime/docker-bake.hcl runtime --set runtime.tags=$RUNTIME_IMAGE --load"
echo "  kind load docker-image $RUNTIME_IMAGE --name $CLUSTER"
