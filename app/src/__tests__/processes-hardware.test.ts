// @vitest-environment node
/**
 * K-1: the hardware-profile reader and the write-gate bounds check
 * (process-compute spec §3, §4). The pipeline runs the same fixture cases.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_HARDWARE_CPU,
  DEFAULT_HARDWARE_PROFILE,
  hardwareBoundsError,
  hardwareProfilesPath,
  loadHardwareProfiles,
  parseHardwareProfiles,
  publicProfiles,
  resetHardwareProfilesCache,
} from "@/lib/processes/hardware";

const fixturePath = fileURLToPath(
  new URL("../../../tests/contract-fixtures/hardware-profiles.json", import.meta.url),
);
const localPath = fileURLToPath(
  new URL("../../../infra/hardware-profiles/local.json", import.meta.url),
);
const fixture = JSON.parse(readFileSync(fixturePath, "utf8")) as {
  document: unknown;
  bounds_cases: {
    name: string;
    hardware: { profile: string; cpu: number; gpu_count: number };
    memory_mb: number;
    app: "accept" | "reject";
    reason: string;
  }[];
};

afterEach(() => resetHardwareProfilesCache());

describe("parseHardwareProfiles", () => {
  it("reads the sample document and keeps backend opaque", () => {
    const set = parseHardwareProfiles(fixture.document);
    expect(set.profiles.map((p) => p.id)).toEqual(["standard", "gpu-l4"]);
    expect(set.profiles[0].cpu.default).toBe(DEFAULT_HARDWARE_CPU);
    expect(publicProfiles(set).every((p) => !("backend" in p))).toBe(true);
  });

  it("the shipped local set agrees with the defaults an absent block gets", () => {
    const set = parseHardwareProfiles(JSON.parse(readFileSync(localPath, "utf8")));
    const standard = set.profiles.find((p) => p.id === DEFAULT_HARDWARE_PROFILE);
    expect(standard?.cpu.default).toBe(DEFAULT_HARDWARE_CPU);
    expect(standard?.gpu_count).toBeNull();
  });
});

describe("loadHardwareProfiles", () => {
  it("prefers PROCESS_HARDWARE_PROFILES_FILE, then the checkout's local set", () => {
    expect(hardwareProfilesPath({ PROCESS_HARDWARE_PROFILES_FILE: "/tmp/x.json" })).toBe("/tmp/x.json");
    expect(hardwareProfilesPath({})).toBe(localPath);
  });

  it("caches per path", () => {
    const a = loadHardwareProfiles({});
    const b = loadHardwareProfiles({});
    expect(b).toBe(a);
    resetHardwareProfilesCache();
    expect(loadHardwareProfiles({})).not.toBe(a);
  });

  it("names the file when it cannot be read", () => {
    expect(() => loadHardwareProfiles({ PROCESS_HARDWARE_PROFILES_FILE: "/nonexistent/hp.json" })).toThrow(
      /nonexistent\/hp\.json/,
    );
  });
});

describe("hardwareBoundsError — the fixture's bounds_cases", () => {
  const set = parseHardwareProfiles(fixture.document);
  it.each(fixture.bounds_cases)("$name", ({ hardware, memory_mb, app, reason }) => {
    const error = hardwareBoundsError(hardware, memory_mb, set);
    if (app === "accept") expect(error).toBeNull();
    else expect(error).toBe(reason);
  });
});
