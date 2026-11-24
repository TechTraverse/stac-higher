// Render harness shared by every helm vitest suite (app/src/__tests__/helm/).
// Runs `helm template` and parses the output; no cluster is involved.
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseAllDocuments } from "yaml";

const HERE = dirname(fileURLToPath(import.meta.url));

/** @type {string} */
export const CHART_DIR = dirname(HERE);
/** @type {string} */
export const VALUES_DIR = join(HERE, "values");
export const KUBE_VERSION = "1.34.0";

/**
 * True when `helm version` runs. Throws under CI when helm is missing.
 * @returns {boolean}
 */
export function helmReady() {
  const r = spawnSync("helm", ["version", "--short"], { encoding: "utf8" });
  const ok = !r.error && r.status === 0;
  if (!ok && process.env.CI) throw new Error("helm is required when CI is set");
  return ok;
}

/**
 * @param {{ valuesFiles?: string[], values?: object, releaseName?: string, namespace?: string }} [opts]
 * @returns {{ ok: boolean, docs: any[], stderr: string }}
 */
export function renderChart({
  valuesFiles = [],
  values = {},
  releaseName = "stac-higher",
  namespace = "stac-higher",
} = {}) {
  const tmp = mkdtempSync(join(tmpdir(), "helm-render-"));
  try {
    const inline = join(tmp, "inline.json");
    writeFileSync(inline, JSON.stringify(values));
    const args = ["template", releaseName, CHART_DIR, "--namespace", namespace, "--kube-version", KUBE_VERSION];
    for (const f of [...valuesFiles, inline]) args.push("-f", f);
    const r = spawnSync("helm", args, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
    if (r.status !== 0) return { ok: false, docs: [], stderr: r.stderr ?? String(r.error) };
    const docs = parseAllDocuments(r.stdout)
      .map((d) => d.toJS())
      .filter((d) => d != null);
    return { ok: true, docs, stderr: r.stderr ?? "" };
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

/**
 * @param {string} name
 * @returns {string[]}
 */
export function scenario(name) {
  const base = join(VALUES_DIR, "base.yaml");
  return name === "base" ? [base] : [base, join(VALUES_DIR, `${name}.yaml`)];
}

/**
 * @param {any[]} docs
 * @returns {{ kind: string, name: string, spec: any, annotations: Record<string, string> }[]}
 */
export function podSpecs(docs) {
  return docs
    .filter((d) => d.kind === "Deployment" || d.kind === "Job")
    .map((d) => ({
      kind: d.kind,
      name: d.metadata.name,
      spec: d.spec.template.spec,
      annotations: d.spec.template.metadata?.annotations ?? {},
    }));
}

/**
 * @param {any} spec
 * @returns {any[]}
 */
export function containersOf(spec) {
  return [...(spec.initContainers ?? []), ...(spec.containers ?? [])];
}

/**
 * Env as Kubernetes resolves it: envFrom ConfigMaps first, explicit env wins.
 * @param {any[]} docs
 * @param {any} container
 * @returns {Record<string, string | { secret: string, key: string } | { configMap: string, key: string }>}
 */
export function effectiveEnv(docs, container) {
  /** @type {Record<string, any>} */
  const out = {};
  for (const from of container.envFrom ?? []) {
    const name = from.configMapRef?.name;
    if (!name) continue;
    const cm = docs.find((d) => d.kind === "ConfigMap" && d.metadata.name === name);
    for (const [k, v] of Object.entries(cm?.data ?? {})) out[k] = String(v);
  }
  for (const e of container.env ?? []) {
    if (e.valueFrom?.secretKeyRef) {
      out[e.name] = { secret: e.valueFrom.secretKeyRef.name, key: e.valueFrom.secretKeyRef.key };
    } else if (e.valueFrom?.configMapKeyRef) {
      out[e.name] = { configMap: e.valueFrom.configMapKeyRef.name, key: e.valueFrom.configMapKeyRef.key };
    } else {
      out[e.name] = e.value === undefined ? "" : String(e.value);
    }
  }
  return out;
}
