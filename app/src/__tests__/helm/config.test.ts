// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { CHART_DIR, helmReady, renderChart, scenario } from "../../../../infra/helm/stac-higher/tests/render.mjs";
import { parseHardwareProfiles } from "@/lib/processes/hardware";
import { credentialBudgetError } from "@/lib/processes/credential-budget";

type Doc = any;
const REPO = join(CHART_DIR, "..", "..", "..");
const base = (values: object) => ({ valuesFiles: scenario("base"), values });
function render(values: object = {}, scen = "base") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
const cm = (docs: Doc[], suffix: string) => docs.find((d) => d.kind === "ConfigMap" && d.metadata.name === `stac-higher-${suffix}`);
const data = (docs: Doc[], suffix: string) => cm(docs, suffix)?.data ?? {};
function fails(values: object, ...needles: string[]) {
  const r = renderChart(base(values));
  expect(r.ok).toBe(false);
  for (const n of needles) expect(r.stderr).toContain(n);
}
const profileDoc = (wait: number) => ({
  version: 1,
  profiles: [
    {
      id: "standard",
      label: "Standard",
      description: "d",
      tier: "cpu",
      accelerator: null,
      cpu: { min: 0.25, max: 4, default: 1 },
      memory_mb: { min: 128, max: 16384, default: 512 },
      gpu_count: null,
      max_queue_wait_seconds: wait,
      image: null,
      backend: { kubernetes: { queue: "process-runs", node_selector: {}, tolerations: [], runtime_class: null, extended_resources: {} } },
    },
  ],
});

describe.skipIf(!helmReady())("chart config layer", () => {
  it("the default hardware profiles are the eks-cpu set and the app accepts them", () => {
    const set = parseHardwareProfiles(JSON.parse(data(render(), "config-files")["hardware-profiles.json"]));
    expect(set.profiles.map((p) => p.id)).toEqual(["standard", "cpu-large"]);
    for (const p of set.profiles) expect((p.backend as any).kubernetes.queue).toBe("process-runs");
  });

  it("every eks-cpu profile fits GeoColor under the AWS cap", () => {
    const seed = readFileSync(join(REPO, "services/pipeline/src/pipeline/demo/goes/seed.py"), "utf8");
    const geocolor = Number(/GEOCOLOR_RUNTIME = \{[^}]*"timeout_seconds": (\d+)/s.exec(seed)![1]);
    const docs = render();
    const set = parseHardwareProfiles(JSON.parse(data(docs, "config-files")["hardware-profiles.json"]));
    for (const p of set.profiles) {
      expect(
        credentialBudgetError({ maxCredentialSeconds: 3600, maxQueueWaitSeconds: p.max_queue_wait_seconds, timeoutSeconds: geocolor, graceSeconds: 300 }),
      ).toBeNull();
    }
    expect(data(docs, "env-shared").PROCESS_MAX_CREDENTIAL_SECONDS).toBe("3600");
  });

  it("the chart's image policy is the repo's default", () => {
    const mine = readFileSync(join(CHART_DIR, "files/image-policy/default.json"));
    expect(mine.equals(readFileSync(join(REPO, "infra/image-policy/default.json")))).toBe(true);
    expect(JSON.parse(data(render(), "config-files")["image-policy.json"])).toEqual(JSON.parse(mine.toString()));
  });

  it("deployment-supplied documents replace the defaults", () => {
    const doc = profileDoc(900);
    const d = data(render({ config: { hardwareProfiles: doc } }), "config-files");
    expect(JSON.parse(d["hardware-profiles.json"])).toEqual(doc);
  });

  it("refuses a profile whose queue wait leaves no time under the cap", () => {
    fails({ config: { hardwareProfiles: profileDoc(3400) } }, "config.hardwareProfiles", "standard");
    const r = renderChart(base({ config: { hardwareProfiles: profileDoc(3400) }, aws: { processMaxCredentialSeconds: null } }));
    expect(r.ok, r.stderr).toBe(true);
  });

  it("shared env carries the executor, the cap, the file paths and a credential-free DATABASE_URL", () => {
    const e = data(render(), "env-shared");
    expect(e.DATABASE_URL).toBe("postgresql://db.example.com:5432/stac_higher");
    expect(e.DATABASE_URL).not.toMatch(/\/\/[^/]*@/);
    expect(e.PROCESS_EXECUTOR).toBe("kubernetes");
    expect(e.PGSSLMODE).toBe("verify-full");
    expect(e.PGSSLROOTCERT).toBe("/etc/stac-higher/db-ca/ca-bundle.pem");
    expect(e.PROCESS_CREDENTIAL_GRACE_SECONDS).toBe("300");
    expect(e.PROCESS_NETWORK_MAX).toBe("isolated");
    expect(e.PROCESS_HARDWARE_PROFILES_FILE).toBe("/etc/stac-higher/config/hardware-profiles.json");
    expect(e.PROCESS_IMAGE_POLICY_FILE).toBe("/etc/stac-higher/config/image-policy.json");
    expect(e.AWS_REGION).toBe("us-east-1");
    expect(e.AWS_DEFAULT_REGION).toBe("us-east-1");
    expect(data(render({ aws: { processMaxCredentialSeconds: null } }), "env-shared")).not.toHaveProperty("PROCESS_MAX_CREDENTIAL_SECONDS");
  });

  it("sslMode disable drops the TLS env and the CA volume", () => {
    const docs = render({ database: { sslMode: "disable", caBundle: { existingConfigMap: "" } } });
    const e = data(docs, "env-shared");
    expect(e).not.toHaveProperty("PGSSLMODE");
    expect(e).not.toHaveProperty("PGSSLROOTCERT");
    expect(cm(docs, "db-ca")).toBeUndefined();
  });

  it("the pipeline's S3 endpoints stay blank on AWS", () => {
    const docs = render();
    const p = data(docs, "env-pipeline");
    expect(p).toHaveProperty("STAGING_S3_ENDPOINT", "");
    expect(p).toHaveProperty("PROCESS_RUN_S3_ENDPOINT", "");
    expect(data(docs, "env-app").S3_ENDPOINT).toBe("https://s3.us-east-1.amazonaws.com");
    const m = data(render({ objectStore: { internalEndpoint: "http://minio.example.com:9000" } }), "env-pipeline");
    expect(m.STAGING_S3_ENDPOINT).toBe("http://minio.example.com:9000");
    expect(m.PROCESS_RUN_S3_ENDPOINT).toBe("http://minio.example.com:9000");
    expect(p.STAGING_BUCKET).toBe("stac-higher-example-platform");
    expect(p.PROCESS_STS_ROLE_ARN).toBe("arn:aws:iam::000000000000:role/stac-higher-example-process-run");
  });

  it("one cap and one grace feed both runtimes", () => {
    const docs = render();
    for (const s of ["env-app", "env-pipeline"]) {
      expect(data(docs, s)).not.toHaveProperty("PROCESS_MAX_CREDENTIAL_SECONDS");
      expect(data(docs, s)).not.toHaveProperty("PROCESS_CREDENTIAL_GRACE_SECONDS");
    }
    expect(data(docs, "env-shared")).toHaveProperty("PROCESS_MAX_CREDENTIAL_SECONDS");
  });

  it("app env derives the OIDC redirect and joins audiences", () => {
    const e = data(render({ publicUrls: { app: "https://app.example.com/" }, auth: { audiences: ["a", "b"] } }), "env-app");
    expect(e.OIDC_REDIRECT_URI).toBe("https://app.example.com/api/auth/callback");
    expect(e.AUTH_BEARER_AUDIENCES).toBe("a,b");
    expect(e.AUTH_BEARER_AUDIENCE_CLAIM).toBe("client_id");
    expect(JSON.parse(e.AUTH_CLAIMS_MAPPING)).toEqual({ sub: "sub", email: "email", groups: "groups" });
    expect(e.S3_BUCKET).toBe("stac-higher-example-platform");
    expect(e.TITILER_URL).toBe("https://titiler.example.com");
    expect(e.BUILTIN_CATALOG_PUBLIC_URL).toBe("https://catalog.example.com");
    expect(e).not.toHaveProperty("BUILTIN_CATALOG_URL");
    expect(e).not.toHaveProperty("SAFE_FETCH_ALLOW_HOSTS");
    expect(e).not.toHaveProperty("AUTH_BYPASS_FORCE");
  });

  it("bypass sets AUTH_BYPASS_FORCE only when allowed", () => {
    const bypass = { auth: { mode: "bypass", allowDevBypass: true, devIdentity: { sub: "dev" } } };
    const e = data(render(bypass), "env-app");
    expect(e.AUTH_MODE).toBe("bypass");
    expect(e.AUTH_BYPASS_FORCE).toBe("true");
    expect(JSON.parse(e.DEV_AUTH_IDENTITY)).toEqual({ sub: "dev" });
    fails({ auth: { mode: "bypass", allowDevBypass: false } }, "auth.allowDevBypass");
  });

  it("the internal catalog host joins SAFE_FETCH_ALLOW_HOSTS", () => {
    const e = data(
      render({ app: { builtinCatalogInternalUrl: "http://stac-higher-auth-proxy:8080", safeFetchAllowHosts: ["x.internal"] } }),
      "env-app",
    );
    expect(e.SAFE_FETCH_ALLOW_HOSTS).toBe("stac-higher-auth-proxy,x.internal");
    expect(e.BUILTIN_CATALOG_URL).toBe("http://stac-higher-auth-proxy:8080");
  });

  it("app.env wins over derived values", () => {
    expect(data(render({ app: { env: { AUTH_MODE: "oidc", FOO: "bar" } } }), "env-app")).toMatchObject({ FOO: "bar" });
  });

  it("pipeline env keeps upstream runtime image refs", () => {
    const e = data(render({}, "digests-mirror"), "env-pipeline");
    for (const k of ["PROCESS_RUNTIME_IMAGE", "PROCESS_RUNTIME_IMAGE_STACTOOLS", "IMAGE_SCANNER_IMAGE"]) {
      expect(e[k]).toMatch(/^ghcr\.io\/techtraverse\/stac-higher-[a-z-]+@sha256:[0-9a-f]{64}$/);
    }
    expect(e.PROCESS_RUNTIME_IMAGE).toContain("stac-higher-process-runtime@");
    expect(e.HOME).toBe("/tmp");
    expect(e.PYTHONDONTWRITEBYTECODE).toBe("1");
    const p = data(render({ pipeline: { egressAllowHosts: ["a.example", "b.example"] } }), "env-pipeline");
    expect(p.EGRESS_ALLOW_HOSTS).toBe("a.example,b.example");
    expect(data(render(), "env-pipeline")).not.toHaveProperty("EGRESS_ALLOW_HOSTS");
  });

  it("refuses secret-looking extra env", () => {
    fails({ app: { env: { MY_TOKEN: "x" } } }, "app.env", "MY_TOKEN");
    fails({ pipeline: { env: { AWS_SECRET_ACCESS_KEY: "x" } } }, "pipeline.env", "AWS_SECRET_ACCESS_KEY");
  });

  it("no static S3 or AWS key is configured", () => {
    for (const s of ["base", "digests-mirror", "irsa", "existing-secrets"]) {
      for (const d of render({}, s).filter((x) => x.kind === "ConfigMap")) {
        for (const k of Object.keys(d.data ?? {})) expect(k).not.toMatch(/ACCESS_KEY_ID|SECRET_ACCESS_KEY/);
      }
    }
  });

  it("an inline CA bundle renders the CA ConfigMap", () => {
    const docs = render({ database: { caBundle: { existingConfigMap: "", pem: "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n" } } });
    expect(cm(docs, "db-ca").data["ca-bundle.pem"]).toContain("BEGIN CERTIFICATE");
    expect(cm(render(), "db-ca")).toBeUndefined();
  });
});
