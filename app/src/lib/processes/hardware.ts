/**
 * Hardware profiles (K-1, process-compute spec §3, ADR 0019).
 *
 * A deployment describes the hardware a run may ask for as named PROFILES in
 * one JSON document both runtimes read. This side validates it STRICTLY (a
 * typo in a deployment file fails at the route, not in a run), serves it to
 * the UI minus each profile's pipeline-only `backend` block, and enforces a
 * revision's `hardware` block against the bounds at the write gate — the
 * pipeline re-checks independently at launch (`pipeline/process/hardware.py`),
 * the `PROCESS_NETWORK_MAX` pattern.
 *
 * The file is read at request time and cached per process lifetime: it is a
 * per-deployment path (`PROCESS_HARDWARE_PROFILES_FILE`), not a document baked
 * into the bundle — unlike `builtin-extractors.json`, which every deployment
 * shares.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { z } from "zod";

export const HARDWARE_TIERS = ["cpu", "cpu-large", "gpu"] as const;
export const DEFAULT_HARDWARE_PROFILE = "standard";
/** What an absent `hardware` block means on BOTH sides — equals the shipped
 * sets' `standard.cpu.default` (tests pin it). */
export const DEFAULT_HARDWARE_CPU = 1;
export const DEFAULT_HARDWARE_GPU_COUNT = 0;

const PROFILE_ID = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export const hardwareBoundsSchema = z
  .object({ min: z.number(), max: z.number(), default: z.number() })
  .strict()
  .refine((b) => b.min <= b.default && b.default <= b.max, {
    message: "bounds must satisfy min <= default <= max",
  });

export const hardwareProfileSchema = z
  .object({
    id: z.string().regex(PROFILE_ID),
    label: z.string().min(1),
    description: z.string(),
    tier: z.enum(HARDWARE_TIERS),
    accelerator: z
      .object({ vendor: z.string().min(1), model: z.string().min(1), memory_gb: z.number().positive() })
      .strict()
      .nullable(),
    cpu: hardwareBoundsSchema,
    memory_mb: hardwareBoundsSchema,
    gpu_count: hardwareBoundsSchema.nullable(),
    max_queue_wait_seconds: z.number().int().min(0),
    image: z.string().min(1).nullable(),
    // Pipeline-only; carried opaquely and never returned by the API.
    backend: z.record(z.string(), z.unknown()),
  })
  .strict()
  .refine((p) => (p.accelerator === null) === (p.gpu_count === null), {
    message: "gpu_count is required with an accelerator and forbidden without one",
    path: ["gpu_count"],
  });

export const hardwareProfileSetSchema = z
  .object({ version: z.literal(1), profiles: z.array(hardwareProfileSchema).min(1) })
  .strict()
  .superRefine((set, ctx) => {
    const ids = set.profiles.map((p) => p.id);
    if (new Set(ids).size !== ids.length) {
      ctx.addIssue({ code: "custom", path: ["profiles"], message: "profile ids must be unique" });
    }
    if (!ids.includes(DEFAULT_HARDWARE_PROFILE)) {
      ctx.addIssue({
        code: "custom",
        path: ["profiles"],
        message: `exactly one profile must be '${DEFAULT_HARDWARE_PROFILE}'`,
      });
    }
  });

export type HardwareProfile = z.infer<typeof hardwareProfileSchema>;
export type HardwareProfileSet = z.infer<typeof hardwareProfileSetSchema>;
export type PublicHardwareProfile = Omit<HardwareProfile, "backend">;

export function parseHardwareProfiles(document: unknown): HardwareProfileSet {
  const parsed = hardwareProfileSetSchema.safeParse(document);
  if (!parsed.success) {
    throw new Error(`hardware profiles: ${parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ")}`);
  }
  return parsed.data;
}

const CHECKOUT_LOCAL_SET = fileURLToPath(
  new URL("../../../../infra/hardware-profiles/local.json", import.meta.url),
);

export function hardwareProfilesPath(env: Record<string, string | undefined> = process.env): string {
  const override = env.PROCESS_HARDWARE_PROFILES_FILE?.trim();
  return override ? override : CHECKOUT_LOCAL_SET;
}

let cache: { path: string; set: HardwareProfileSet } | null = null;

/** Read and validate the deployment's profile set, cached per process
 * lifetime (the document is deployment config, not operator data — spec §13.1). */
export function loadHardwareProfiles(
  env: Record<string, string | undefined> = process.env,
): HardwareProfileSet {
  const path = hardwareProfilesPath(env);
  if (cache && cache.path === path) return cache.set;
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    throw new Error(`could not read the hardware profiles at ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
  const set = parseHardwareProfiles(JSON.parse(text));
  cache = { path, set };
  return set;
}

export function resetHardwareProfilesCache(): void {
  cache = null;
}

export function findProfile(set: HardwareProfileSet, id: string): HardwareProfile | undefined {
  return set.profiles.find((p) => p.id === id);
}

/** JS prints `0.25` and `4.5` the way Python's `str(0.25)` does — the fixture
 * pins both sides against the same numbers. */
function fmt(n: number): string {
  return String(n);
}

/** The write-gate half of the dual enforcement (spec §4). Returns the message
 * to refuse with, or null when the block is within its profile's bounds — the
 * SAME text `check_hardware_bounds` raises in the pipeline (the fixture's
 * `bounds_cases[].reason` pins both). */
export function hardwareBoundsError(
  hardware: { profile: string; cpu: number; gpu_count: number },
  memoryMb: number,
  set: HardwareProfileSet,
): string | null {
  const profile = findProfile(set, hardware.profile);
  if (!profile) return `hardware.profile '${hardware.profile}' is not a hardware profile of this deployment`;
  if (hardware.cpu < profile.cpu.min || hardware.cpu > profile.cpu.max) {
    return `hardware.cpu ${fmt(hardware.cpu)} is outside profile '${profile.id}' bounds ${fmt(profile.cpu.min)}–${fmt(profile.cpu.max)}`;
  }
  if (profile.gpu_count === null) {
    if (hardware.gpu_count !== 0) return `hardware.gpu_count must be 0: profile '${profile.id}' has no accelerator`;
  } else if (hardware.gpu_count < profile.gpu_count.min || hardware.gpu_count > profile.gpu_count.max) {
    return `hardware.gpu_count ${hardware.gpu_count} is outside profile '${profile.id}' bounds ${fmt(profile.gpu_count.min)}–${fmt(profile.gpu_count.max)}`;
  }
  if (memoryMb < profile.memory_mb.min || memoryMb > profile.memory_mb.max) {
    return `memory_mb ${memoryMb} is outside profile '${profile.id}' bounds ${fmt(profile.memory_mb.min)}–${fmt(profile.memory_mb.max)}`;
  }
  return null;
}

export function publicProfiles(set: HardwareProfileSet): PublicHardwareProfile[] {
  return set.profiles.map(({ backend: _backend, ...rest }) => rest);
}
