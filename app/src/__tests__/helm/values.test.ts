// @vitest-environment node
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parse, stringify } from "yaml";
import { IMAGE_KEYS } from "../../../../.github/scripts/release-manifest.mjs";
import {
  CHART_DIR,
  VALUES_DIR,
  helmReady,
  renderChart,
  scenario,
} from "../../../../infra/helm/stac-higher/tests/render.mjs";

const ROLE_ANN = "eks.amazonaws.com/role-arn";
const D = (c: string) => "sha256:" + c.repeat(64);

type Doc = { kind: string; metadata: { name: string; annotations?: Record<string, string> }; automountServiceAccountToken?: boolean };
const sas = (docs: Doc[]) => docs.filter((d) => d.kind === "ServiceAccount");
const sa = (docs: Doc[], name: string) => sas(docs).find((d) => d.metadata.name === name);

function fails(opts: Parameters<typeof renderChart>[0], needle: string) {
  const r = renderChart(opts);
  expect(r.ok).toBe(false);
  expect(r.stderr).toContain(needle);
}
const base = (values: object, extra: Partial<Parameters<typeof renderChart>[0]> = {}) => ({
  valuesFiles: scenario("base"),
  values,
  ...extra,
});

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

describe.skipIf(!helmReady())("chart values contract", () => {
  it("renders the base scenario", () => {
    const r = renderChart({ valuesFiles: scenario("base") });
    expect(r.stderr).toBe("");
    expect(r.ok).toBe(true);
  });

  it.each(["base", "existing-secrets", "irsa", "digests-mirror"])("renders scenario %s", (name) => {
    const r = renderChart({ valuesFiles: scenario(name) });
    expect(r.ok, r.stderr).toBe(true);
    for (const d of r.docs as Doc[] & { apiVersion?: string }[]) {
      expect((d as any).apiVersion).toBeTruthy();
      expect(d.kind).toBeTruthy();
      expect(d.metadata.name).toBeTruthy();
    }
  });

  it("refuses a missing required value", () => {
    fails(base({ publicUrls: { app: null } }), "publicUrls");
  });

  it("refuses an unknown secrets mode", () => {
    fails(base({ secrets: { mode: "inline" } }), "secrets");
  });

  it("refuses a typo inside the contract", () => {
    fails(base({ publicUrls: { catalgo: "https://x.example.com" } }), "catalgo");
  });

  it("refuses dev-bypass without the override", () => {
    fails(base({ auth: { mode: "bypass" } }), "auth.allowDevBypass");
    const r = renderChart(base({ auth: { mode: "bypass", allowDevBypass: true } }));
    expect(r.ok, r.stderr).toBe(true);
  });

  it("requires the OIDC issuer and client in oidc mode", () => {
    fails(base({ auth: { issuer: "" } }), "auth.issuer");
    fails(base({ auth: { clientId: "" } }), "auth.clientId");
  });

  it("requires role ARNs in irsa mode", () => {
    fails(
      { valuesFiles: scenario("irsa"), values: { identity: { roleArns: { app: "" } } } },
      "identity.roleArns.app",
    );
    fails(
      { valuesFiles: scenario("irsa"), values: { identity: { roleArns: { pipeline: "" } } } },
      "identity.roleArns.pipeline",
    );
  });

  it("pod-identity ServiceAccounts carry no role annotation; irsa ones do", () => {
    const pi = renderChart({ valuesFiles: scenario("base") }).docs as Doc[];
    for (const n of ["stac-higher-app", "stac-higher-pipeline"]) {
      expect(sa(pi, n), n).toBeTruthy();
      expect(sa(pi, n)!.metadata.annotations?.[ROLE_ANN]).toBeUndefined();
    }
    expect(sa(pi, "stac-higher-app")!.automountServiceAccountToken).toBe(false);
    expect(sa(pi, "stac-higher-pipeline")!.automountServiceAccountToken).toBe(true);
    const irsa = renderChart({ valuesFiles: scenario("irsa") }).docs as Doc[];
    expect(sa(irsa, "stac-higher-app")!.metadata.annotations?.[ROLE_ANN]).toBe(
      "arn:aws:iam::000000000000:role/stac-higher-example-app",
    );
    expect(sa(irsa, "stac-higher-pipeline")!.metadata.annotations?.[ROLE_ANN]).toBe(
      "arn:aws:iam::000000000000:role/stac-higher-example-pipeline",
    );
  });

  it("ServiceAccounts take R-5's names, not the release name", () => {
    const docs = renderChart(base({}, { releaseName: "other" })).docs as Doc[];
    expect(sas(docs).map((d) => d.metadata.name).sort()).toEqual(["stac-higher-app", "stac-higher-pipeline"]);
    const c = renderChart(base({ identity: { serviceAccounts: { app: "custom-app" } } })).docs as Doc[];
    expect(sa(c, "custom-app")).toBeTruthy();
  });

  it("refuses a namespace the Pod Identity associations do not name", () => {
    fails(base({}, { namespace: "elsewhere" }), "identity.namespace");
  });

  describe("R-5 helm_values", () => {
    const r5 = [join(VALUES_DIR, "r5-helm-values.yaml"), join(VALUES_DIR, "r5-deployment.yaml")];
    it("accepts R-5's helm_values verbatim", () => {
      const r = renderChart({ valuesFiles: r5 });
      expect(r.ok, r.stderr).toBe(true);
    });
    it("renders with the optional R-5 sections omitted", () => {
      // R-5 omits `auth` and `demoDestination` when off (never null). The chart
      // defaults then apply; oidc without an issuer is refused, so the deployment
      // supplies the dev-bypass override here.
      const tmp = mkdtempSync(join(tmpdir(), "r5-omit-"));
      try {
        const doc = parse(readFileSync(r5[0], "utf8"));
        delete doc.auth;
        delete doc.demoDestination;
        const f = join(tmp, "r5-omitted.yaml");
        writeFileSync(f, stringify(doc));
        const r = renderChart({
          valuesFiles: [f, r5[1]],
          values: { auth: { mode: "bypass", allowDevBypass: true } },
        });
        expect(r.ok, r.stderr).toBe(true);
        fails({ valuesFiles: [f, r5[1]] }, "auth.issuer");
      } finally {
        rmSync(tmp, { recursive: true, force: true });
      }
    });
    it("rejects a renamed R-5 key", () => {
      fails({ valuesFiles: r5, values: { aws: { buckett: "x" } } }, "buckett");
    });
  });

  describe("image digests", () => {
    it("the chart's image keys equal the release manifest's", () => {
      const helpers = readFileSync(join(CHART_DIR, "templates/_helpers.tpl"), "utf8");
      const m = helpers.match(/define "stac-higher\.imageKeys"[\s\S]*?-}}\s*(\[[^\]]*\])/);
      expect(m).toBeTruthy();
      expect(JSON.parse(m![1])).toEqual([...IMAGE_KEYS]);
      for (const key of IMAGE_KEYS) {
        const all = Object.fromEntries(IMAGE_KEYS.map((k: string) => [k, D("a")]));
        const r = renderChart(base({ global: { imageDigests: all } }));
        expect(r.ok, `${key}: ${r.stderr}`).toBe(true);
      }
      fails(base({ global: { imageDigests: { bogus: D("a") } } }), "global.imageDigests.bogus");
    });
    it("refuses a malformed digest", () => {
      fails(base({ global: { imageDigests: { app: "sha256:abc" } } }), "global.imageDigests.app");
    });
    it("refuses partial digests", () => {
      fails(base({ global: { imageDigests: { app: D("a") } } }), "global.imageDigests.pipeline");
    });
  });

  it("refuses an app URL with a path", () => {
    fails(base({ publicUrls: { app: "https://example.com/stac" } }), "publicUrls.app");
  });

  it("verify-full needs exactly one CA source", () => {
    fails(base({ database: { caBundle: { existingConfigMap: "" } } }), "database.caBundle");
    fails(base({ database: { caBundle: { pem: "x" } } }), "database.caBundle");
  });

  it("uses neither lookup nor install-only branches", () => {
    const banned = /\b(lookup|IsInstall|IsUpgrade|rand\w*|now|uuidv4|genCA|genPrivateKey|genSelfSignedCert|genSignedCert)\b/;
    for (const f of walk(join(CHART_DIR, "templates"))) {
      expect(readFileSync(f, "utf8"), f).not.toMatch(banned);
    }
  });
});
