// @vitest-environment node
import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  IMAGE_KEYS,
  VERSION_RE,
  buildReleaseManifest,
  readDigestsDir,
} from "../../../.github/scripts/release-manifest.mjs";

const SCRIPT = fileURLToPath(
  new URL("../../../.github/scripts/release-manifest.mjs", import.meta.url),
);
const PACKAGE_CHART = fileURLToPath(new URL("../../../scripts/package-chart.sh", import.meta.url));
const ACCEPTED_VERSIONS = [
  "v0.1.0", "v1.2.3-rc.1", "v1.2.3-0", "v1.2.3-alpha-1", "v1.2.3-0a.1",
  "v1.2.3--", "v0.0.0-dryrun.42", "v10.20.30-x.7.z.92",
];
// The last four are accepted by the old pattern but rejected by Helm.
const REJECTED_VERSIONS = [
  "v1", "v0.1", "0.1.0", "v1.2.3+build", "v01.2.3", "v1.2.3-", "V1.2.3",
  "v1.2.3-rc..1", "v1.2.3-01", "v1.2.3-.rc", "v1.2.3-rc.",
];
const SHA = "0123456789abcdef0123456789abcdef01234567";
const REPO = "TechTraverse/stac-higher";
const D = (c: string) => "sha256:" + c.repeat(64);
const HEX = "0123456789abcdef";

function makeDigests(): Record<string, string> {
  return Object.fromEntries(IMAGE_KEYS.map((k: string, i: number) => [k, D(HEX[i])]));
}

function input(over: Record<string, unknown> = {}) {
  return { version: "v0.1.0", gitSha: SHA, repository: REPO, digests: makeDigests(), ...over };
}

function writeDigestDir(digests: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "relman-"));
  for (const [k, v] of Object.entries(digests)) writeFileSync(join(dir, k), v + "\n");
  return dir;
}

describe("release-manifest", () => {
  it("builds the contract shape for a release tag", () => {
    const digests = makeDigests();
    const m = buildReleaseManifest(input({ digests }));
    expect(m).toEqual({
      schemaVersion: 1,
      version: "v0.1.0",
      gitSha: SHA,
      images: Object.fromEntries(
        IMAGE_KEYS.map((k: string) => [
          k,
          { ref: `ghcr.io/techtraverse/stac-higher-${k}:0.1.0`, digest: digests[k] },
        ]),
      ),
      chart: { ref: "oci://ghcr.io/techtraverse/charts/stac-higher", version: "0.1.0" },
      terraformModules: {
        ref: "git::https://github.com/TechTraverse/stac-higher.git//infra/terraform/modules/stac-higher?ref=v0.1.0",
      },
    });
    expect(Object.keys(m.images)).toEqual([...IMAGE_KEYS]);
    expect([...IMAGE_KEYS]).toEqual([
      "app", "pipeline", "titiler", "cube-server", "image-scanner",
      "process-runtime", "process-runtime-stactools", "auth-proxy",
    ]);
  });

  it("keeps a prerelease suffix in tags and chart version", () => {
    const m = buildReleaseManifest(input({ version: "v0.2.0-rc.1" }));
    expect(m.images.app.ref).toBe("ghcr.io/techtraverse/stac-higher-app:0.2.0-rc.1");
    expect(m.chart.version).toBe("0.2.0-rc.1");
    expect(m.terraformModules.ref).toMatch(/\?ref=v0\.2\.0-rc\.1$/);
  });

  it("rejects non-semver versions", () => {
    for (const version of REJECTED_VERSIONS) {
      expect(() => buildReleaseManifest(input({ version }))).toThrow(/version/);
      expect(() => buildReleaseManifest(input({ version }))).toThrow(version);
    }
  });

  it("accepts SemVer 2.0 pre-releases", () => {
    for (const version of ACCEPTED_VERSIONS) {
      expect(buildReleaseManifest(input({ version })).version).toBe(version);
    }
  });

  it("matches the chart packager's tag rule exactly", () => {
    // scripts/package-chart.sh repeats VERSION_RE in bash; the two must agree,
    // or a tag could ship images and a manifest without a chart (R-9).
    for (const version of [...ACCEPTED_VERSIONS, ...REJECTED_VERSIONS]) {
      const r = spawnSync("bash", [PACKAGE_CHART, "--check-tag", version], { encoding: "utf8" });
      expect({ version, ok: r.status === 0 }).toEqual({ version, ok: VERSION_RE.test(version) });
    }
  });

  it("adds chart.digest when the chart digest is given", () => {
    const m = buildReleaseManifest(input({ chartDigest: D("c") }));
    expect(m.chart).toEqual({
      ref: "oci://ghcr.io/techtraverse/charts/stac-higher",
      version: "0.1.0",
      digest: D("c"),
    });
  });

  it("rejects a malformed chart digest", () => {
    for (const chartDigest of ["", "abc", "sha256:" + "A".repeat(64)]) {
      expect(() => buildReleaseManifest(input({ chartDigest }))).toThrow(/chart digest/);
    }
  });

  it("rejects a missing image", () => {
    const digests = makeDigests();
    delete digests["auth-proxy"];
    expect(() => buildReleaseManifest(input({ digests }))).toThrow(/auth-proxy/);
  });

  it("rejects an unexpected image", () => {
    const digests = makeDigests();
    digests["titiler-old"] = D("a");
    expect(() => buildReleaseManifest(input({ digests }))).toThrow(/titiler-old/);
  });

  it("rejects a malformed digest", () => {
    for (const bad of ["sha256:" + "ABCDEF01".repeat(8), "abc", ""]) {
      const digests = makeDigests();
      digests["cube-server"] = bad;
      expect(() => buildReleaseManifest(input({ digests }))).toThrow(/cube-server/);
    }
  });

  it("reports every problem at once", () => {
    const digests = makeDigests();
    delete digests.app;
    let msg = "";
    try {
      buildReleaseManifest(input({ gitSha: "nope", digests }));
    } catch (e) {
      msg = (e as Error).message;
    }
    expect(msg).toMatch(/gitSha/);
    expect(msg).toMatch(/app/);
    expect(msg.split("\n").length).toBeGreaterThanOrEqual(2);
  });

  it("rejects a malformed repository or sha", () => {
    expect(() => buildReleaseManifest(input({ repository: "stac-higher" }))).toThrow(/repository/);
    expect(() => buildReleaseManifest(input({ repository: "a/b/c" }))).toThrow(/repository/);
    expect(() => buildReleaseManifest(input({ gitSha: SHA.slice(0, 39) }))).toThrow(/gitSha/);
  });

  it("reads one digest file per image and trims whitespace", () => {
    const digests = makeDigests();
    expect(readDigestsDir(writeDigestDir(digests))).toEqual(digests);
  });

  it("CLI writes the manifest and exits 0", () => {
    const digests = makeDigests();
    const dir = writeDigestDir(digests);
    const out = join(mkdtempSync(join(tmpdir(), "relman-out-")), "release-manifest.json");
    execFileSync(process.execPath, [
      SCRIPT, "--version", "v0.1.0", "--git-sha", SHA, "--repository", REPO,
      "--digests-dir", dir, "--out", out,
    ]);
    const text = readFileSync(out, "utf8");
    expect(text.endsWith("\n")).toBe(true);
    expect(JSON.parse(text).images.app.digest).toBe(digests.app);
  });

  it("CLI exits 1 with a message when a digest file is missing", () => {
    const digests = makeDigests();
    delete digests["auth-proxy"];
    const dir = writeDigestDir(digests);
    const out = join(mkdtempSync(join(tmpdir(), "relman-out-")), "m.json");
    const r = spawnSync(process.execPath, [
      SCRIPT, "--version", "v0.1.0", "--git-sha", SHA, "--repository", REPO,
      "--digests-dir", dir, "--out", out,
    ], { encoding: "utf8" });
    expect(r.status).toBe(1);
    expect(r.stderr).toMatch(/^release-manifest: .*auth-proxy/m);
  });
});
