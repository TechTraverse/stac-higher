/**
 * Cube sink form model (Z-8b): the Settings card's string-valued form, mapped
 * onto the strict PUT schema so client and server cannot disagree on what is
 * valid. Names are comma-separated; an empty window field means unset.
 */
import { z } from "zod";
import type { CubeSinkApiError, CubeSinkPutBody } from "./queries";
import { cubeSinkPutSchema, type CubeSinkConfig } from "./schemas";
import type { ApiCubeSink } from "./storage";

export interface CubeSinkFormValues {
  source_collection_id: string;
  append_dim: string;
  /** Comma-separated. */
  variables: string;
  /** Comma-separated. */
  loadable_variables: string;
  /** "" = unset. */
  max_steps: string;
  /** "" = unset. */
  max_age: string;
}

/** Spec §3.1 example (Decision 6). */
export const CUBE_FORM_DEFAULTS: CubeSinkFormValues = {
  source_collection_id: "",
  append_dim: "t",
  variables: "CMI, DQF",
  loadable_variables: "t, x, y, goes_imager_projection",
  max_steps: "288",
  max_age: "24h",
};

export type CubeSinkFormField = keyof CubeSinkFormValues;
export type CubeSinkFormErrors = Partial<Record<CubeSinkFormField | "root", string>>;

const names = (value: string) =>
  value
    .split(",")
    .map((n) => n.trim())
    .filter((n) => n !== "");

export function sinkToForm(sink: ApiCubeSink): CubeSinkFormValues {
  const { config } = sink;
  return {
    source_collection_id: sink.source_collection_id,
    append_dim: config.append_dim,
    variables: config.variables.join(", "),
    loadable_variables: config.loadable_variables.join(", "),
    max_steps:
      config.window?.max_steps === undefined ? "" : String(config.window.max_steps),
    max_age: config.window?.max_age ?? "",
  };
}

/** `enabled` is always sent: the schema would default an omitted one to true. */
export function formToPutBody(
  values: CubeSinkFormValues,
  enabled: boolean,
  existing?: CubeSinkConfig,
): CubeSinkPutBody {
  const maxSteps = values.max_steps.trim();
  const maxAge = values.max_age.trim();
  const window: { max_steps?: number; max_age?: string } = {};
  if (maxSteps !== "") window.max_steps = Number(maxSteps);
  if (maxAge !== "") window.max_age = maxAge;
  return {
    source_collection_id: values.source_collection_id.trim(),
    enabled,
    config: {
      parser: existing?.parser ?? "hdf5",
      append_dim: values.append_dim.trim(),
      variables: names(values.variables),
      loadable_variables: names(values.loadable_variables),
      asset_key: existing?.asset_key ?? "cube",
      ...(Object.keys(window).length > 0 ? { window } : {}),
      on_late: existing?.on_late ?? "skip",
    },
  };
}

/** Server (zod) issue paths -> the form field that shows them. */
export function apiIssuesToFormErrors(
  details: CubeSinkApiError["details"],
): CubeSinkFormErrors {
  const out: CubeSinkFormErrors = {};
  for (const { path, message } of details ?? []) {
    const [a, b, c] = path;
    let field: CubeSinkFormField | "root" = "root";
    if (path.length === 1 && a === "source_collection_id") field = "source_collection_id";
    else if (a === "config" && b === "append_dim") field = "append_dim";
    else if (a === "config" && b === "variables") field = "variables";
    else if (a === "config" && b === "loadable_variables") field = "loadable_variables";
    else if (a === "config" && b === "window") {
      field = c === "max_age" ? "max_age" : "max_steps";
    }
    out[field] ??= message;
  }
  return out;
}

/** Field-level messages from the PUT schema itself (enabled is irrelevant). */
export const cubeSinkFormSchema: z.ZodType<
  CubeSinkFormValues,
  CubeSinkFormValues
> = z
  .object({
    source_collection_id: z.string(),
    append_dim: z.string(),
    variables: z.string(),
    loadable_variables: z.string(),
    max_steps: z.string(),
    max_age: z.string(),
  })
  .superRefine((values, ctx) => {
    const parsed = cubeSinkPutSchema.safeParse(formToPutBody(values, true));
    if (parsed.success) return;
    const errors = apiIssuesToFormErrors(
      parsed.error.issues.map((i) => ({
        path: i.path.filter((p): p is string | number => typeof p !== "symbol"),
        message: i.message,
      })),
    );
    for (const [field, message] of Object.entries(errors)) {
      ctx.addIssue({ code: "custom", path: [field], message });
    }
  });
