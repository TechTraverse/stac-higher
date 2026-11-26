"""Narrow Kueue's admission webhooks to the runs namespace (K-6 review R3).

Usage (from the repo root):
  uv run --no-project --with pyyaml python -I scripts/kueue-narrow-webhooks.py \
      <manifests.yaml> <out.yaml> [--namespace stac-higher-runs] \
      [--expect infra/kueue/webhooks.txt]

Kueue's release manifests register every framework webhook (pods,
Deployments, StatefulSets, Jobs, LeaderWorkerSets, …) with
`failurePolicy: Fail` on every namespace but kube-system and kueue-system,
even for integrations the configuration disables (they answer no-op). While
Kueue's webhook is down, nothing that matches can be created or rolled
anywhere in the cluster. The platform runs Kueue-managed workloads in ONE
namespace, so every webhook that carries a namespaceSelector gets
`matchLabels: {kubernetes.io/metadata.name: <namespace>}` instead.

Webhooks without a namespaceSelector are left alone; each must be on Kueue's
own API group (cluster-scoped ClusterQueues, ResourceFlavors, Cohorts, and
the Workloads Kueue itself writes), else this refuses. Only the two webhook
configuration documents are rewritten; every other document is copied byte
for byte. Kueue never rewrites a selector: its cert rotator updates only each
webhook's `clientConfig.caBundle` (open-policy-agent/cert-controller,
`injectCertToWebhook`).

`--expect FILE` compares the release's webhooks with a committed list
(`<name> narrowed|kueue-only` per line), so a Kueue bump that adds, drops or
re-scopes a webhook fails until the list (and this review) is updated."""

from __future__ import annotations

import argparse
import re
import sys

import yaml

KINDS = ("MutatingWebhookConfiguration", "ValidatingWebhookConfiguration")
KUEUE_GROUP = "kueue.x-k8s.io"


def narrow(text: str, namespace: str) -> tuple[str, dict[str, str]]:
    """The rewritten manifests and ``{webhook name: narrowed|kueue-only}``."""
    parts = re.split(r"(?m)^---[ \t]*$", text)
    seen: dict[str, str] = {}
    out = []
    for part in parts:
        doc = yaml.safe_load(part) if part.strip() else None
        if not isinstance(doc, dict) or doc.get("kind") not in KINDS:
            out.append(part)
            continue
        for hook in doc.get("webhooks") or []:
            name = hook["name"]
            if "namespaceSelector" in hook:
                hook["namespaceSelector"] = {
                    "matchLabels": {"kubernetes.io/metadata.name": namespace}
                }
                seen[name] = "narrowed"
                continue
            groups = {g for rule in hook.get("rules") or [] for g in rule.get("apiGroups") or []}
            if groups != {KUEUE_GROUP}:
                raise SystemExit(
                    f"kueue-narrow-webhooks: {name} has no namespaceSelector and matches "
                    f"{sorted(groups)}, not only {KUEUE_GROUP}: review it before installing"
                )
            seen[name] = "kueue-only"
        out.append("\n" + yaml.safe_dump(doc, sort_keys=False))
    return "---".join(out), seen


def read_expected(path: str) -> dict[str, str]:
    expected = {}
    with open(path) as handle:
        lines = handle.read().splitlines()
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if line:
            name, treatment = line.split()
            expected[name] = treatment
    return expected


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests")
    parser.add_argument("out")
    parser.add_argument("--namespace", default="stac-higher-runs")
    parser.add_argument("--expect")
    args = parser.parse_args(argv[1:])
    with open(args.manifests) as handle:
        text, seen = narrow(handle.read(), args.namespace)
    if not seen:
        raise SystemExit("kueue-narrow-webhooks: no webhook configuration found")
    if args.expect:
        expected = read_expected(args.expect)
        if seen != expected:
            added = sorted(set(seen) - set(expected))
            dropped = sorted(set(expected) - set(seen))
            changed = sorted(n for n in set(seen) & set(expected) if seen[n] != expected[n])
            raise SystemExit(
                f"kueue-narrow-webhooks: the release's webhooks differ from {args.expect}: "
                f"added {added}, dropped {dropped}, re-scoped {changed}"
            )
    with open(args.out, "w") as handle:
        handle.write(text)
    narrowed = sum(1 for v in seen.values() if v == "narrowed")
    print(f"kueue-narrow-webhooks: {narrowed} webhooks narrowed to {args.namespace}, "
          f"{len(seen) - narrowed} on Kueue's own API left as released")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
