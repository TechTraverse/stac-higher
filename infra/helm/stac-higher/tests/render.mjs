// Render harness shared by every helm vitest suite (app/src/__tests__/helm/).
// Runs `helm template` and parses the output; no cluster is involved.
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseAllDocuments } from "yaml";

const HERE = dirname(fileURLToPath(import.meta.url));

/** @type {string} */
export const CHART_DIR = dirname(HERE);
/** @type {string} */
export const VALUES_DIR = join(HERE, "values");
/** The repository root (the chart is infra/helm/stac-higher). @type {string} */
export const REPO_DIR = join(CHART_DIR, "..", "..", "..");
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

/**
 * Every YAML document of every `.yaml` file in a directory (sorted by file name), empty documents dropped.
 * @param {string} dir
 * @returns {any[]}
 */
export function loadYamlDir(dir) {
  return readdirSync(dir)
    .filter((f) => f.endsWith(".yaml"))
    .sort()
    .flatMap((f) => parseAllDocuments(readFileSync(join(dir, f), "utf8")).map((d) => d.toJS()))
    .filter((d) => d != null);
}

const CHART_LABEL = /^(helm\.sh\/|app\.kubernetes\.io\/)/;
const CHART_ANNOTATION = /^(helm\.sh\/|argocd\.argoproj\.io\/)/;

/**
 * A deep copy without the chart's own metadata: `helm.sh/*` and `app.kubernetes.io/*` labels,
 * `helm.sh/*` and `argocd.argoproj.io/*` annotations; an emptied `labels`/`annotations` is dropped.
 * @param {any} doc
 * @returns {any}
 */
export function stripChartMetadata(doc) {
  const out = structuredClone(doc);
  for (const [field, re] of /** @type {const} */ ([["labels", CHART_LABEL], ["annotations", CHART_ANNOTATION]])) {
    const m = out.metadata?.[field];
    if (!m) continue;
    for (const k of Object.keys(m)) if (re.test(k)) delete m[k];
    if (Object.keys(m).length === 0) delete out.metadata[field];
  }
  return out;
}

/**
 * @param {string} ip dotted IPv4
 * @returns {number}
 */
function ipv4ToInt(ip) {
  const parts = ip.split(".").map(Number);
  if (parts.length !== 4 || parts.some((p) => !Number.isInteger(p) || p < 0 || p > 255)) {
    throw new Error(`not an IPv4 address: ${ip}`);
  }
  return parts.reduce((acc, p) => acc * 256 + p, 0);
}

/**
 * True when the IPv4 CIDR contains the address (pure integer math).
 * @param {string} cidr
 * @param {string} ip
 * @returns {boolean}
 */
export function ipv4Contains(cidr, ip) {
  const [net, bitsText] = cidr.split("/");
  const bits = Number(bitsText ?? 32);
  if (!Number.isInteger(bits) || bits < 0 || bits > 32) throw new Error(`not an IPv4 CIDR: ${cidr}`);
  const size = 2 ** (32 - bits);
  return Math.floor(ipv4ToInt(net) / size) === Math.floor(ipv4ToInt(ip) / size);
}

/**
 * True when every address of `inner` is in `outer` (both IPv4 CIDRs).
 * @param {string} outer
 * @param {string} inner
 * @returns {boolean}
 */
export function ipv4Covers(outer, inner) {
  const [net, bits] = inner.split("/");
  return Number(outer.split("/")[1] ?? 32) <= Number(bits ?? 32) && ipv4Contains(outer, net);
}

/**
 * True when an ipBlock peer admits any address of `range`: the CIDRs overlap and no `except` entry covers the
 * overlap (the narrower of the two).
 * @param {{ cidr: string, except?: string[] }} block
 * @param {string} range
 * @returns {boolean}
 */
export function ipBlockReaches(block, range) {
  const overlap = ipv4Covers(block.cidr, range) ? range : ipv4Covers(range, block.cidr) ? block.cidr : null;
  if (overlap === null) return false;
  return !(block.except ?? []).some((e) => ipv4Covers(e, overlap));
}
