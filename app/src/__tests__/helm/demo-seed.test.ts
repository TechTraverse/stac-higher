// @vitest-environment node
// R-10 (GitHub #146): the one-shot demo seed Job, off by default.
import { describe, expect, it } from "vitest";
import { helmReady, renderChart, scenario, containersOf, effectiveEnv } from "../../../../infra/helm/stac-higher/tests/render.mjs";

type Doc = any;
function render(values: object = {}, scen = "demo-seed") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok, r.stderr).toBe(true);
  return r.docs;
}
function fails(values: object, needle: string, scen = "demo-seed") {
  const r = renderChart({ valuesFiles: scenario(scen), values });
  expect(r.ok).toBe(false);
  expect(r.stderr).toContain(needle);
}
const jobs = (docs: Doc[]) => docs.filter((d) => d.kind === "Job" && d.metadata.name.includes("demo-seed"));
const job = (docs: Doc[]) => {
  const all = jobs(docs);
  expect(all).toHaveLength(1);
  return all[0];
};
const podOf = (docs: Doc[]) => job(docs).spec.template.spec;
const byName = (docs: Doc[], name: string) => containersOf(podOf(docs)).find((c: any) => c.name === name);
const DEMO = ["python", "-m", "pipeline.demo", "--in-cluster"];
const EXISTING = { platform: "p", dbMaster: "m", dbOwner: "own", dbStacApi: "a", dbStacReader: "r", dbProxyPolicy: "x", dbCubeServer: "c" };

describe.skipIf(!helmReady())("chart demo seed Job", () => {
  it("is off by default", () => {
    const docs = render({}, "base");
    expect(jobs(docs)).toEqual([]);
    expect(docs.find((d: Doc) => d.kind === "ExternalSecret" && d.metadata.name.includes("demo"))).toBeUndefined();
    // the R-5 scenario carries demoDestination, which alone renders nothing
    const r5 = renderChart({ valuesFiles: [scenario("base")[0].replace("base.yaml", "r5-helm-values.yaml"), scenario("base")[0].replace("base.yaml", "r5-deployment.yaml")] });
    expect(r5.ok, r5.stderr).toBe(true);
    expect(jobs(r5.docs)).toEqual([]);
  });

  it("is a plain Job, not a hook, that never cleans itself up", () => {
    const j = job(render());
    expect(j.metadata.annotations?.["helm.sh/hook"]).toBeUndefined();
    expect(j.metadata.annotations?.["argocd.argoproj.io/hook"]).toBeUndefined();
    expect(j.spec.ttlSecondsAfterFinished).toBeUndefined();
    expect(j.spec.backoffLimit).toBe(6);
    expect(j.spec.activeDeadlineSeconds).toBe(3600);
    expect(j.spec.template.spec.restartPolicy).toBe("Never");
    expect(j.metadata.labels["app.kubernetes.io/component"]).toBe("demo-seed");
    expect(j.spec.template.metadata.labels["app.kubernetes.io/component"]).toBe("demo-seed");
  });

  it("the Job name is a stable hash of its pod", () => {
    const a = job(render()).metadata.name;
    expect(a).toMatch(/^stac-higher-demo-seed-[0-9a-f]{8}$/);
    expect(job(render()).metadata.name).toBe(a);
    expect(job(render({ demoSeed: { extraArgs: { "goes-seed": ["--window", "-3h"] } } })).metadata.name).not.toBe(a);
  });

  it("a new image re-runs the seed; another workload's knob does not", () => {
    const a = job(render()).metadata.name;
    expect(job(render({ global: { imageTag: "9.9.9" } })).metadata.name).not.toBe(a);
    expect(job(render({ app: { replicas: 5 } })).metadata.name).toBe(a);
    // the longest allowed fullname still fits an object name
    const long = job(render({ fullnameOverride: "x".repeat(42) })).metadata.name;
    expect(long.length).toBeLessThanOrEqual(63);
  });

  it("runs the chosen seeds in the fixed order, one container each", () => {
    const pod = podOf(render({ demoSeed: { seeds: ["images-seed", "seed", "goes-seed", "cube-seed"] } }));
    expect(pod.initContainers.map((c: any) => c.name)).toEqual(["seed", "goes-seed", "cube-seed"]);
    expect(pod.containers.map((c: any) => c.name)).toEqual(["images-seed"]);
    const one = podOf(render({ demoSeed: { seeds: ["goes-seed"], deliver: false } }));
    expect(one.initContainers ?? []).toEqual([]);
    expect(one.containers.map((c: any) => c.name)).toEqual(["goes-seed"]);
  });

  it("every container runs pipeline.demo --in-cluster without building images", () => {
    const docs = render();
    for (const c of containersOf(podOf(docs))) {
      expect(c.image).toBe("ghcr.io/techtraverse/stac-higher-pipeline:0.1.0");
      expect(c.command.slice(0, 5)).toEqual([...DEMO, c.name]);
    }
    expect(byName(docs, "seed").command).toEqual([...DEMO, "seed", "--no-build-image"]);
    expect(byName(docs, "goes-seed").command).toEqual([...DEMO, "goes-seed", "--no-build-image", "--deliver", "--window", "-2h"]);
    expect(byName(docs, "cube-seed").command).toEqual([...DEMO, "cube-seed"]);
    expect(byName(docs, "images-seed").command).toEqual([...DEMO, "images-seed"]);
    const noDeliver = render({ demoSeed: { deliver: false } });
    expect(byName(noDeliver, "goes-seed").command).not.toContain("--deliver");
  });

  it("reads the pipeline's config and runtime Secrets, never hook copies", () => {
    const docs = render();
    for (const c of containersOf(podOf(docs))) {
      expect(c.envFrom.map((e: any) => e.configMapRef.name)).toEqual(["stac-higher-env-shared", "stac-higher-env-pipeline"]);
      const env = effectiveEnv(docs, c);
      expect(env.DEMO_STAC_URL).toBe("http://stac-higher-stac:8080");
      expect(env.DEMO_APP_URL).toBe("http://stac-higher-app");
      expect(env.DEMO_PUBLIC_APP_URL).toBe("https://app.example.com");
      expect(env.DEMO_PUBLIC_TITILER_URL).toBe("https://titiler.example.com");
      expect(String(env.DATABASE_URL)).toMatch(/^postgresql:\/\/[^@/]+:\d+\/[^@/]+$/);
      expect(env.STAGING_BUCKET).toBe("stac-higher-example-platform");
      expect(env.STAGING_S3_ENDPOINT).toBe("");
      expect(env.PGUSER).toEqual({ secret: "stac-higher-db-owner", key: "username" });
      expect(env.PGPASSWORD).toEqual({ secret: "stac-higher-db-owner", key: "password" });
      expect(env.CREDENTIALS_MASTER_KEY).toEqual({ secret: "stac-higher-platform", key: "CREDENTIALS_MASTER_KEY" });
      for (const e of c.env) if (e.valueFrom?.secretKeyRef) expect(e.valueFrom.secretKeyRef.name).not.toMatch(/-hook-/);
    }
  });

  it("secrets reach only the container that needs them", () => {
    const docs = render();
    const goes = effectiveEnv(docs, byName(docs, "goes-seed"));
    expect(goes.DEMO_DESTINATION_BUCKET).toBe("stac-higher-example-demo");
    expect(goes.DEMO_DESTINATION_REGION).toBe("us-east-1");
    expect(goes.DEMO_DESTINATION_ACCESS_KEY_ID).toEqual({ secret: "stac-higher-demo-destination", key: "access_key_id" });
    expect(goes.DEMO_DESTINATION_SECRET_ACCESS_KEY).toEqual({ secret: "stac-higher-demo-destination", key: "secret_access_key" });
    expect(goes.STAC_HIGHER_BEARER).toBeUndefined();
    const images = effectiveEnv(docs, byName(docs, "images-seed"));
    expect(images.STAC_HIGHER_BEARER).toEqual({ secret: "stac-higher-demo-bearer", key: "token" });
    expect(images.DEMO_DESTINATION_ACCESS_KEY_ID).toBeUndefined();
    for (const n of ["seed", "cube-seed"]) {
      const env = effectiveEnv(docs, byName(docs, n));
      expect(env.STAC_HIGHER_BEARER).toBeUndefined();
      expect(env.DEMO_DESTINATION_SECRET_ACCESS_KEY).toBeUndefined();
    }
    const off = render({ demoSeed: { deliver: false } });
    expect(effectiveEnv(off, byName(off, "goes-seed")).DEMO_DESTINATION_ACCESS_KEY_ID).toBeUndefined();
  });

  it("the destination region falls back to aws.region", () => {
    const docs = render({ demoDestination: { region: "" }, aws: { region: "us-west-2" } });
    expect(effectiveEnv(docs, byName(docs, "goes-seed")).DEMO_DESTINATION_REGION).toBe("us-west-2");
  });

  it("the destination key comes from Secrets Manager through a runtime ExternalSecret", () => {
    const docs = render();
    const es = docs.find((d: Doc) => d.kind === "ExternalSecret" && d.metadata.name === "stac-higher-demo-destination");
    expect(es).toBeDefined();
    expect(es.metadata.annotations?.["helm.sh/hook"]).toBeUndefined();
    expect(es.spec.target.name).toBe("stac-higher-demo-destination");
    expect(es.spec.data.map((d: any) => [d.secretKey, d.remoteRef.key, d.remoteRef.property])).toEqual([
      ["access_key_id", "arn:aws:secretsmanager:us-east-1:000000000000:secret:stac-higher-example/demo/destination_credentials-AbCdEf", "access_key_id"],
      ["secret_access_key", "arn:aws:secretsmanager:us-east-1:000000000000:secret:stac-higher-example/demo/destination_credentials-AbCdEf", "secret_access_key"],
    ]);
    const off = render({ demoSeed: { deliver: false } });
    expect(off.find((d: Doc) => d.kind === "ExternalSecret" && d.metadata.name === "stac-higher-demo-destination")).toBeUndefined();
  });

  it("existing mode reads the deployment's destination Secret", () => {
    const docs = render({ secrets: { mode: "existing", existing: EXISTING }, demoSeed: { existingDestinationSecret: "my-demo-dest" } });
    expect(docs.find((d: Doc) => d.kind === "ExternalSecret")).toBeUndefined();
    const env = effectiveEnv(docs, byName(docs, "goes-seed"));
    expect(env.DEMO_DESTINATION_ACCESS_KEY_ID).toEqual({ secret: "my-demo-dest", key: "access_key_id" });
    expect(env.PGUSER).toEqual({ secret: "own", key: "username" });
    expect(env.CREDENTIALS_MASTER_KEY).toEqual({ secret: "p", key: "CREDENTIALS_MASTER_KEY" });
  });

  it("the pod is hardened and runs as the pipeline's identity", () => {
    const docs = render();
    const pod = podOf(docs);
    expect(pod.serviceAccountName).toBe("stac-higher-pipeline");
    expect(pod.automountServiceAccountToken).toBe(false);
    expect(pod.securityContext).toMatchObject({ runAsNonRoot: true, runAsUser: 999, runAsGroup: 999, fsGroup: 999, seccompProfile: { type: "RuntimeDefault" } });
    for (const c of containersOf(pod)) {
      expect(c.securityContext).toMatchObject({ allowPrivilegeEscalation: false, readOnlyRootFilesystem: true, capabilities: { drop: ["ALL"] } });
      expect(c.resources).toEqual({ requests: { cpu: "100m", memory: "512Mi" }, limits: { memory: "1Gi" } });
      const mounts = Object.fromEntries(c.volumeMounts.map((m: any) => [m.mountPath, m.name]));
      expect(mounts["/tmp"]).toBe("tmp");
      expect(mounts["/etc/stac-higher/db-ca"]).toBe("db-ca");
    }
    const ca = pod.volumes.find((v: any) => v.name === "db-ca");
    expect(ca.configMap.name).toBe("rds-ca-bundle"); // runtime CA, never the hook copy
    const sched = podOf(render({ pipeline: { nodeSelector: { pool: "system" }, tolerations: [{ key: "k", operator: "Exists" }] } }));
    expect(sched.nodeSelector).toEqual({ pool: "system" });
    expect(sched.tolerations).toEqual([{ key: "k", operator: "Exists" }]);
  });

  // One render per case: a helm exec each, kept under vitest's per-test timeout.
  it.each<[string, object, string]>([
    ["no STAC URL", { demoSeed: { stacUrl: "" } }, "demoSeed.stacUrl"],
    ["a STAC URL without a scheme", { demoSeed: { stacUrl: "stac:8080" } }, "demoSeed.stacUrl"],
    ["images-seed without a bearer", { demoSeed: { appBearer: { secretName: "" } } }, "demoSeed.appBearer.secretName"],
    ["deliver without goes-seed", { demoSeed: { seeds: ["seed"] } }, "demoSeed.deliver"],
    ["deliver without a bucket", { demoDestination: { bucket: "" } }, "demoDestination.bucket"],
    ["deliver without the secret ARN", { demoDestination: { credentialsSecretArn: "" } }, "demoDestination.credentialsSecretArn"],
    ["deliver in existing mode without a Secret", { secrets: { mode: "existing", existing: EXISTING } }, "demoSeed.existingDestinationSecret"],
    ["extraArgs for a seed not chosen", { demoSeed: { extraArgs: { "cube-sed": ["--x"] } } }, "demoSeed.extraArgs.cube-sed"],
    ["an unknown seed", { demoSeed: { seeds: ["seed", "nope"] } }, "seeds"],
    ["no seeds", { demoSeed: { seeds: [] } }, "seeds"],
    ["a duplicate seed", { demoSeed: { seeds: ["seed", "seed"] } }, "seeds"],
  ])("refuses %s, naming the key", (_name, values, needle) => {
    fails(values, needle);
  });

  it("images-seed needs no bearer when it is not chosen", () => {
    // maps merge across values files, so the scenario's goes-seed extraArgs is nulled out
    const docs = render({ demoSeed: { seeds: ["seed"], deliver: false, appBearer: { secretName: "" }, extraArgs: { "goes-seed": null } } });
    expect(containersOf(podOf(docs)).map((c: any) => c.name)).toEqual(["seed"]);
  });
});
