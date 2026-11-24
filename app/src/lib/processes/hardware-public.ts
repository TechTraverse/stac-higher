/**
 * The hardware-profile vocabulary the UI reads (K-2, process-compute spec
 * §3.3 / §9). This is the CLIENT-SAFE half of K-1's `hardware.ts`: that
 * module reads the deployment's profile file with `node:fs` and must never
 * reach the browser bundle, so the public shape and the pure formatters live
 * here and `hardware.ts` asserts (at the type level) that what its route
 * returns is exactly this.
 */
import type { ProcessHardware } from "./schemas";

export type HardwareTier = "cpu" | "cpu-large" | "gpu";

export interface HardwareBounds {
  min: number;
  max: number;
  default: number;
}

/** One profile as `GET /api/processes/hardware-profiles` returns it — a
 * K-1 profile minus its pipeline-only `backend` block. */
export interface PublicHardwareProfile {
  id: string;
  label: string;
  description: string;
  tier: HardwareTier;
  accelerator: { vendor: string; model: string; memory_gb: number } | null;
  cpu: HardwareBounds;
  memory_mb: HardwareBounds;
  gpu_count: HardwareBounds | null;
  max_queue_wait_seconds: number;
  image: string | null;
}

export interface HardwareProfilesResponse {
  backend: string;
  profiles: PublicHardwareProfile[];
}

/** What a runtime with no `hardware` block means (spec §4) — the write
 * gate's default, repeated here because the form starts from it. */
export const DEFAULT_HARDWARE: ProcessHardware = { profile: "standard", cpu: 1, gpu_count: 0 };

export const TIER_ORDER: readonly HardwareTier[] = ["cpu", "cpu-large", "gpu"];

export const TIER_LABELS: Record<HardwareTier, string> = {
  cpu: "CPU",
  "cpu-large": "Large CPU",
  gpu: "GPU",
};

export function findPublicProfile(
  profiles: readonly PublicHardwareProfile[],
  id: string,
): PublicHardwareProfile | undefined {
  return profiles.find((p) => p.id === id);
}

/** What the form resets to when a profile is chosen (spec §9). */
export function profileDefaults(p: PublicHardwareProfile): {
  hardware: ProcessHardware;
  memoryMb: number;
} {
  return {
    hardware: { profile: p.id, cpu: p.cpu.default, gpu_count: p.gpu_count?.default ?? 0 },
    memoryMb: p.memory_mb.default,
  };
}

/** Profile documents name accelerators by lowercase id (`nvidia` / `l4`);
 * the UI shows them as people write them. */
function acceleratorModel(p: PublicHardwareProfile): string {
  return p.accelerator ? p.accelerator.model.toUpperCase() : "GPU";
}

/** The accelerator badge: `NVIDIA L4 · 24 GB`. */
export function acceleratorLabel(p: PublicHardwareProfile): string | null {
  if (!p.accelerator) return null;
  return `${p.accelerator.vendor.toUpperCase()} ${acceleratorModel(p)} · ${p.accelerator.memory_gb} GB`;
}

function trim(n: number): string {
  // Two decimals at most; String() already drops a trailing `.0`.
  return String(Math.round(n * 100) / 100);
}

export function formatMemoryMb(mb: number): string {
  return mb < 1024 ? `${mb} MB` : `${trim(mb / 1024)} GB`;
}

export function formatWait(seconds: number): string {
  if (seconds <= 0) return "no wait";
  if (seconds < 120) return `${seconds} s`;
  if (seconds < 3600) return `${trim(seconds / 60)} min`;
  return `${trim(seconds / 3600)} h`;
}

/** The one-line summary under the fieldset and on a run row:
 * `4 vCPU · 16 GB · 1× L4 · waits up to 2 h`. With no profile it says the
 * profile is not in this deployment — unless the profiles have not loaded
 * yet, when it claims nothing either way. */
export function hardwareSummary(
  p: PublicHardwareProfile | undefined,
  hardware: ProcessHardware,
  memoryMb: number,
  { profilesLoaded = true }: { profilesLoaded?: boolean } = {},
): string {
  const parts: string[] = [];
  if (!p && profilesLoaded) parts.push(`${hardware.profile} (not in this deployment)`);
  parts.push(`${trim(hardware.cpu)} vCPU`, formatMemoryMb(memoryMb));
  if (p && hardware.gpu_count > 0) parts.push(`${hardware.gpu_count}× ${acceleratorModel(p)}`);
  if (p) parts.push(`waits up to ${formatWait(p.max_queue_wait_seconds)}`);
  return parts.join(" · ");
}
