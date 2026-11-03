// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import { helmReady, renderChart, scenario } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
const SCENARIOS = ["base", "existing-secrets", "irsa", "digests-mirror"];
const base = (values: object) => ({ valuesFiles: scenario("base"), values });
const byName = (docs: Doc[], name: string) => docs.find((d) => d.kind === "ExternalSecret" && d.metadata.name === name);
function fails(opts: Parameters<typeof renderChart>[0], needle: string) {
  const r = renderChart(opts);
  expect(r.ok).toBe(false);
  expect(r.stderr).toContain(needle);
}

describe.skipIf(!helmReady())("chart secrets layer", () => {
  it.each(SCENARIOS)("renders no Secret in scenario %s", (name) => {
    const r = renderChart({ valuesFiles: scenario(name) });
    expect(r.ok, r.stderr).toBe(true);
    expect(r.docs.filter((d) => d.kind === "Secret")).toEqual([]);
  });

  it("external-secrets mode renders the runtime platform and owner ExternalSecrets", () => {
    const r = renderChart({ valuesFiles: scenario("base") });
    expect(r.ok, r.stderr).toBe(true);
    const ess = r.docs.filter((d) => d.kind === "ExternalSecret");
    const full = "stac-higher";
    const platform = byName(r.docs, `${full}-platform`);
    const owner = byName(r.docs, `${full}-db-owner`);
    expect(platform).toBeTruthy();
    expect(owner).toBeTruthy();
    expect(byName(r.docs, `${full}-dockerhub`)).toBeUndefined();
    for (const es of ess.filter((e) => [platform, owner].includes(e))) {
      expect(es.apiVersion).toBe("external-secrets.io/v1");
      expect(es.spec.secretStoreRef).toEqual({ name: "aws-secrets-manager", kind: "ClusterSecretStore" });
      expect(es.spec.refreshInterval).toBe("1h");
      expect(es.spec.target.creationPolicy).toBe("Owner");
      expect(es.spec.target.deletionPolicy).toBe("Retain");
      for (const d of es.spec.data) {
        expect(d.remoteRef.conversionStrategy).toBe("Default");
        expect(d.remoteRef.decodingStrategy).toBe("None");
        expect(d.remoteRef.metadataPolicy).toBe("None");
      }
    }
    expect(platform.spec.target.name).toBe(`${full}-platform`);
    expect(platform.spec.data.map((d: Doc) => d.secretKey)).toEqual([
      "SESSION_SECRET",
      "CATALOG_BFF_SHARED_SECRET",
      "INTERNAL_API_TOKEN",
      "CREDENTIALS_MASTER_KEY",
    ]);
    for (const d of platform.spec.data) expect(d.remoteRef.property).toBeUndefined();
    const baseValues = parse(readFileSync(scenario("base")[0], "utf8"));
    const arns = ["session_secret", "catalog_bff_shared_secret", "internal_api_token", "credentials_master_key"];
    expect(platform.spec.data.map((d: Doc) => d.remoteRef.key)).toEqual(arns.map((k) => baseValues.secrets.arns[k] as string));
    expect(owner.spec.data.map((d: Doc) => [d.secretKey, d.remoteRef.property])).toEqual([
      ["username", "username"],
      ["password", "password"],
    ]);
    for (const d of owner.spec.data) expect(d.remoteRef.key).toBe(baseValues.secrets.arns.db_stac_higher_owner);
  });

  it("never wires PROXY_AUTH_TOKEN", () => {
    const r = renderChart({ valuesFiles: scenario("base") });
    const text = JSON.stringify(r.docs);
    expect(text).not.toContain("PROXY_AUTH_TOKEN");
    expect(text).not.toContain("proxy_auth_token");
  });

  it("existing mode renders no ExternalSecret", () => {
    const r = renderChart({ valuesFiles: scenario("existing-secrets") });
    expect(r.ok, r.stderr).toBe(true);
    expect(r.docs.filter((d) => d.kind === "ExternalSecret")).toEqual([]);
  });

  it("requires every wired remote key", () => {
    fails(base({ secrets: { arns: { session_secret: "" } } }), "secrets.arns.session_secret");
    fails(base({ database: { masterSecretArn: "" } }), "database.masterSecretArn");
    fails(base({ secrets: { externalSecrets: { storeRef: { name: "" } } } }), "secrets.externalSecrets.storeRef.name");
    const r = renderChart(base({ secrets: { arns: { proxy_auth_token: "" } } }));
    expect(r.ok, r.stderr).toBe(true);
  });

  it("requires every existing Secret name", () => {
    fails(
      { valuesFiles: scenario("existing-secrets"), values: { secrets: { existing: { dbCubeServer: "" } } } },
      "secrets.existing.dbCubeServer",
    );
  });

  it("the Docker Hub credential is optional", () => {
    const hub = "arn:aws:secretsmanager:us-east-1:000000000000:secret:stac-higher-example/dockerhub-AbCdEf";
    const r = renderChart(base({ secrets: { dockerHubArn: hub } }));
    expect(r.ok, r.stderr).toBe(true);
    const es = byName(r.docs, "stac-higher-dockerhub");
    expect(es).toBeTruthy();
    expect(es.spec.data.map((d: Doc) => [d.secretKey, d.remoteRef.key, d.remoteRef.property])).toEqual([
      ["username", hub, "username"],
      ["token", hub, "token"],
    ]);
  });
});
