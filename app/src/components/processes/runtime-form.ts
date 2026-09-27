/**
 * The deploy form's Runtime chooser (C-3, container-images spec §9.3), as
 * pure state: the three kinds of spec §3, the current revision synced in,
 * and the payload the write gate accepts. The gate (`processRuntimeSchema`
 * + `checkImageGate`) stays the authority. This only keeps the form from
 * sending what it would refuse on shape.
 */
import { imageSnapshotSchema, type ImageSnapshot } from "@/lib/images/reference";
import { formatCommand, parseCommand } from "@/lib/processes/command";
import {
  PROCESS_RUNTIME_IMAGE_ALIASES,
  type ProcessNetwork,
  type ProcessRuntime,
  type ProcessRuntimeKind,
  type RuntimeImageAlias,
} from "@/lib/processes/schemas";

export interface RuntimeFormState {
  kind: ProcessRuntimeKind;
  /** Kind 1 only: the platform image alias. */
  runtimeImage: RuntimeImageAlias;
  /** Kinds 2–3: the snapshot taken from the picked registry row. */
  image: ImageSnapshot | null;
  /** Kind 3 only: the Cmd override as typed. */
  commandText: string;
}

export const DEFAULT_RUNTIME_FORM: RuntimeFormState = {
  kind: "inline_python",
  runtimeImage: "default",
  image: null,
  commandText: "",
};

export function runtimeFormFromRevision(
  runtime: Record<string, unknown> | null | undefined,
): RuntimeFormState {
  if (!runtime) return DEFAULT_RUNTIME_FORM;
  const kind = runtime.kind;
  if (kind === "inline_python_on_image" || kind === "container") {
    const snapshot = imageSnapshotSchema.safeParse(runtime.image);
    const command =
      kind === "container" &&
      Array.isArray(runtime.command) &&
      runtime.command.every((entry) => typeof entry === "string")
        ? formatCommand(runtime.command as string[])
        : "";
    return {
      kind,
      runtimeImage: "default",
      image: snapshot.success ? snapshot.data : null,
      commandText: command,
    };
  }
  if (kind !== "inline_python") return DEFAULT_RUNTIME_FORM;
  const alias = (PROCESS_RUNTIME_IMAGE_ALIASES as readonly string[]).includes(
    String(runtime.runtime_image),
  )
    ? (runtime.runtime_image as RuntimeImageAlias)
    : "default";
  return { ...DEFAULT_RUNTIME_FORM, runtimeImage: alias };
}

export interface RuntimeLimits {
  memory_mb: number;
  timeout_seconds: number;
  network: ProcessNetwork;
}

export type RuntimePayload =
  | { ok: true; runtime: ProcessRuntime; carriesCode: boolean }
  | { ok: false; error: string };

export function buildRuntimePayload(form: RuntimeFormState, limits: RuntimeLimits): RuntimePayload {
  const common = {
    memory_mb: limits.memory_mb,
    timeout_seconds: limits.timeout_seconds,
    retry: { max_attempts: 3, backoff: "exponential" as const },
    network: limits.network,
    // K-1: no hardware picker in this form yet (K-2 adds it) — the schema's
    // own default profile at its default cpu, no GPU.
    hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
  };
  if (form.kind === "inline_python") {
    return {
      ok: true,
      carriesCode: true,
      runtime: { kind: "inline_python", image: null, runtime_image: form.runtimeImage, ...common },
    };
  }
  if (!form.image) return { ok: false, error: "Choose an approved image" };
  if (form.kind === "inline_python_on_image") {
    return {
      ok: true,
      carriesCode: true,
      runtime: { kind: "inline_python_on_image", image: form.image, runtime_image: null, ...common },
    };
  }
  const parsed = parseCommand(form.commandText);
  if (parsed.error) return { ok: false, error: parsed.error };
  return {
    ok: true,
    carriesCode: false,
    runtime: {
      kind: "container",
      image: form.image,
      runtime_image: null,
      command: parsed.command,
      ...common,
    },
  };
}

/** The digest a revision pins, for the run rows (kinds 2–3 only). */
export function revisionImageDigest(runtime: Record<string, unknown>): string | null {
  const snapshot = imageSnapshotSchema.safeParse(runtime.image);
  return snapshot.success ? snapshot.data.digest : null;
}
