#!/usr/bin/env bash
# Collect what explains a failed kind leg (K-6). Usage:
#   scripts/kind-diagnostics.sh <out-dir> <admin-kubeconfig>
# Every command may fail (the cluster may be half up): none stops the rest.
#
# NEVER collects Secrets: a run's Secret is its complete environment,
# credentials included (K5-R3). Nothing here runs `get secrets`, and the
# pod dumps carry only `envFrom` references, never values.
set -uo pipefail

out=${1:?usage: scripts/kind-diagnostics.sh <out-dir> <admin-kubeconfig>}
kc=${2:?usage: scripts/kind-diagnostics.sh <out-dir> <admin-kubeconfig>}
mkdir -p "$out"
RUNS_NS=stac-higher-runs

k() { kubectl --kubeconfig "$kc" "$@"; }
save() { # <file> <command...>
  local file=$1
  shift
  "$@" >"$out/$file" 2>&1 || echo "(exit $?)" >>"$out/$file"
}

kind export logs "$out/kind-logs" --name stac-higher >/dev/null 2>&1 || true
save versions.txt k version
save nodes.txt k get nodes -o wide
save node-describe.txt k describe nodes
save runs-objects.yaml k -n "$RUNS_NS" get jobs,pods,workloads.kueue.x-k8s.io,localqueues.kueue.x-k8s.io -o yaml
save runs-events.txt k -n "$RUNS_NS" get events --sort-by=.lastTimestamp
save runs-pods-describe.txt k -n "$RUNS_NS" describe pods
save all-events.txt k get events -A --sort-by=.lastTimestamp
save networkpolicies.yaml k get networkpolicies -A -o yaml
save kueue-cluster-objects.yaml k get clusterqueues,resourceflavors,workloadpriorityclasses -o yaml
save admission-policies.yaml k get validatingadmissionpolicies,validatingadmissionpolicybindings -o yaml
save kueue-controller.log k -n kueue-system logs deploy/kueue-controller-manager --tail=3000
save kueue-config.yaml k -n kueue-system get configmap kueue-manager-config -o yaml
save kueue-system-pods.txt k -n kueue-system get pods -o wide
save kindnet.log k -n kube-system logs -l app=kindnet --tail=1000 --prefix
save pods-all.txt k get pods -A -o wide
echo "kind-diagnostics: written to $out"
