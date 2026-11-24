// @vitest-environment node
/**
 * K-2: the client-safe hardware vocabulary the deploy form and the run rows
 * format with (process-compute spec §3.3, §9).
 */
import { describe, expect, it } from "vitest";
import {
  acceleratorLabel,
  DEFAULT_HARDWARE,
  findPublicProfile,
  formatMemoryMb,
  formatWait,
  hardwareSummary,
  profileDefaults,
  TIER_LABELS,
  TIER_ORDER,
  type PublicHardwareProfile,
} from "@/lib/processes/hardware-public";

const STANDARD: PublicHardwareProfile = {
  id: "standard",
  label: "Standard",
  description: "General purpose.",
  tier: "cpu",
  accelerator: null,
  cpu: { min: 0.25, max: 4, default: 1 },
  memory_mb: { min: 128, max: 16384, default: 512 },
  gpu_count: null,
  max_queue_wait_seconds: 1800,
  image: null,
};

// Vendor and model are lowercase ids in the profile document (the contract
// fixture's `gpu-l4`); the UI shows them uppercased.
const GPU_L4: PublicHardwareProfile = {
  id: "gpu-l4",
  label: "GPU — NVIDIA L4 (24 GB)",
  description: "Single L4.",
  tier: "gpu",
  accelerator: { vendor: "nvidia", model: "l4", memory_gb: 24 },
  cpu: { min: 2, max: 16, default: 4 },
  memory_mb: { min: 4096, max: 65536, default: 16384 },
  gpu_count: { min: 1, max: 1, default: 1 },
  max_queue_wait_seconds: 7200,
  image: "process-runtime-cuda",
};

describe("tier vocabulary", () => {
  it("labels the three tiers in spec order", () => {
    expect(TIER_ORDER).toEqual(["cpu", "cpu-large", "gpu"]);
    expect(TIER_LABELS).toEqual({ cpu: "CPU", "cpu-large": "Large CPU", gpu: "GPU" });
  });

  it("defaults an absent hardware block to standard at 1 CPU, no GPU", () => {
    expect(DEFAULT_HARDWARE).toEqual({ profile: "standard", cpu: 1, gpu_count: 0 });
  });
});

describe("profileDefaults", () => {
  it("is the profile's default cpu / memory and gpu 0 without an accelerator", () => {
    expect(profileDefaults(STANDARD)).toEqual({
      hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
      memoryMb: 512,
    });
  });
  it("takes gpu_count.default with an accelerator", () => {
    expect(profileDefaults(GPU_L4)).toEqual({
      hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 },
      memoryMb: 16384,
    });
  });
});

describe("formatters", () => {
  it("formats memory in MB below 1 GB and in GB above, trimming .0", () => {
    expect(formatMemoryMb(512)).toBe("512 MB");
    expect(formatMemoryMb(1024)).toBe("1 GB");
    expect(formatMemoryMb(1536)).toBe("1.5 GB");
    expect(formatMemoryMb(16384)).toBe("16 GB");
  });
  it("formats the queue wait in s / min / h", () => {
    expect(formatWait(0)).toBe("no wait");
    expect(formatWait(90)).toBe("90 s");
    expect(formatWait(1800)).toBe("30 min");
    expect(formatWait(5400)).toBe("1.5 h");
    expect(formatWait(7200)).toBe("2 h");
  });
  it("labels an accelerator uppercased and returns null without one", () => {
    expect(acceleratorLabel(GPU_L4)).toBe("NVIDIA L4 · 24 GB");
    expect(acceleratorLabel(STANDARD)).toBeNull();
  });
});

describe("hardwareSummary", () => {
  it("reads vCPU · memory · GPUs · wait for a GPU profile", () => {
    expect(hardwareSummary(GPU_L4, { profile: "gpu-l4", cpu: 4, gpu_count: 1 }, 16384)).toBe(
      "4 vCPU · 16 GB · 1× L4 · waits up to 2 h",
    );
  });
  it("omits the GPU segment when gpu_count is 0", () => {
    expect(hardwareSummary(STANDARD, { profile: "standard", cpu: 0.5, gpu_count: 0 }, 512)).toBe(
      "0.5 vCPU · 512 MB · waits up to 30 min",
    );
  });
  it("says so when the profile is not in this deployment", () => {
    expect(hardwareSummary(undefined, { profile: "gone", cpu: 1, gpu_count: 0 }, 512)).toBe(
      "gone (not in this deployment) · 1 vCPU · 512 MB",
    );
  });
  it("claims nothing about the deployment while the profiles are still loading", () => {
    expect(
      hardwareSummary(undefined, { profile: "standard", cpu: 1, gpu_count: 0 }, 512, {
        profilesLoaded: false,
      }),
    ).toBe("1 vCPU · 512 MB");
  });
});

describe("findPublicProfile", () => {
  it("finds by id", () => {
    expect(findPublicProfile([STANDARD, GPU_L4], "gpu-l4")).toBe(GPU_L4);
    expect(findPublicProfile([STANDARD], "gpu-l4")).toBeUndefined();
  });
});
