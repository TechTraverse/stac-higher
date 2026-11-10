/** Cube sink form model (Z-8b): form values <-> the strict PUT schema. */
import { describe, it, expect } from "vitest";
import {
  CUBE_FORM_DEFAULTS,
  apiIssuesToFormErrors,
  cubeSinkFormSchema,
  formToPutBody,
  sinkToForm,
} from "@/lib/cubes/form";
import { cubeSinkPutSchema } from "@/lib/cubes/schemas";
import type { ApiCubeSink } from "@/lib/cubes/storage";

const withSource = (over: Partial<typeof CUBE_FORM_DEFAULTS> = {}) => ({
  ...CUBE_FORM_DEFAULTS,
  source_collection_id: "src",
  ...over,
});

function errorsOf(values: ReturnType<typeof withSource>) {
  const parsed = cubeSinkFormSchema.safeParse(values);
  if (parsed.success) return {};
  const out: Record<string, string> = {};
  for (const issue of parsed.error.issues) out[String(issue.path[0])] = issue.message;
  return out;
}

describe("formToPutBody", () => {
  it("builds the spec §3.1 body from the defaults", () => {
    expect(formToPutBody(withSource(), true)).toEqual({
      source_collection_id: "src",
      enabled: true,
      config: {
        parser: "hdf5",
        append_dim: "t",
        variables: ["CMI", "DQF"],
        loadable_variables: ["t", "x", "y", "goes_imager_projection"],
        asset_key: "cube",
        window: { max_steps: 288, max_age: "24h" },
        on_late: "skip",
      },
    });
    expect(cubeSinkPutSchema.safeParse(formToPutBody(withSource(), true)).success).toBe(true);
  });

  it("omits window when both fields are empty", () => {
    const body = formToPutBody(withSource({ max_steps: "", max_age: "" }), false);
    expect(body.config).not.toHaveProperty("window");
    expect(body.enabled).toBe(false);
  });

  it("trims and drops empty names", () => {
    const body = formToPutBody(withSource({ variables: " CMI , ,DQF " }), true);
    expect(body.config.variables).toEqual(["CMI", "DQF"]);
  });

  it("preserves asset_key, on_late and parser from the existing config", () => {
    const existing = {
      parser: "hdf5" as const,
      append_dim: "t",
      variables: ["CMI"],
      loadable_variables: ["t"],
      asset_key: "mycube",
      on_late: "skip" as const,
    };
    const body = formToPutBody(withSource(), true, existing);
    expect(body.config.asset_key).toBe("mycube");
  });
});

describe("cubeSinkFormSchema", () => {
  it("accepts the defaults with a source", () => {
    expect(cubeSinkFormSchema.safeParse(withSource()).success).toBe(true);
  });
  it("requires a source", () => {
    expect(errorsOf(withSource({ source_collection_id: "" }))).toHaveProperty("source_collection_id");
  });
  it("rejects max_steps 0", () => {
    expect(errorsOf(withSource({ max_steps: "0" }))).toHaveProperty("max_steps");
  });
  it("rejects max_age above 30d", () => {
    expect(errorsOf(withSource({ max_age: "31d" }))).toHaveProperty("max_age");
  });
  it("requires loadable_variables to include the append dim", () => {
    expect(errorsOf(withSource({ loadable_variables: "x, y" }))).toHaveProperty("loadable_variables");
  });
});

describe("sinkToForm", () => {
  it("round-trips a stored config", () => {
    const sink = {
      source_collection_id: "src",
      config: {
        parser: "hdf5",
        append_dim: "time",
        variables: ["A", "B"],
        loadable_variables: ["time", "x"],
        asset_key: "cube",
        window: { max_age: "6h" },
        on_late: "skip",
      },
    } as unknown as ApiCubeSink;
    const form = sinkToForm(sink);
    expect(form).toEqual({
      source_collection_id: "src",
      append_dim: "time",
      variables: "A, B",
      loadable_variables: "time, x",
      max_steps: "",
      max_age: "6h",
    });
    expect(formToPutBody(form, true, sink.config).config).toEqual(sink.config);
  });
});

describe("apiIssuesToFormErrors", () => {
  it("maps every path", () => {
    const m = (path: (string | number)[]) =>
      Object.keys(apiIssuesToFormErrors([{ path, message: "x" }]))[0];
    expect(m(["source_collection_id"])).toBe("source_collection_id");
    expect(m(["config", "append_dim"])).toBe("append_dim");
    expect(m(["config", "variables", 1])).toBe("variables");
    expect(m(["config", "loadable_variables"])).toBe("loadable_variables");
    expect(m(["config", "window", "max_steps"])).toBe("max_steps");
    expect(m(["config", "window", "max_age"])).toBe("max_age");
    expect(m(["config", "window"])).toBe("max_steps");
    expect(m(["enabled"])).toBe("root");
    expect(apiIssuesToFormErrors(undefined)).toEqual({});
  });
  it("keeps the first message per field", () => {
    const e = apiIssuesToFormErrors([
      { path: ["config", "append_dim"], message: "first" },
      { path: ["config", "append_dim"], message: "second" },
    ]);
    expect(e.append_dim).toBe("first");
  });
});
