/**
 * X-4 building blocks: the bundled registry, the process template, the
 * picker's grouping filter, and the guard mapping for the new verb.
 */
import { describe, it, expect } from "vitest";
import { builtinExtractors, findBuiltinExtractor } from "@/lib/extractors/registry";
import {
  builtinProcessBody,
  builtinProcessDefaults,
  builtinRevisionTemplate,
} from "@/lib/extractors/template";
import { builtinSupportsGrouping } from "@/components/collections/IngestFormDialog";
import { processRevisionCreateSchema } from "@/lib/processes/schemas";
import { matchGatedRoute } from "@/lib/authz/permissions";

describe("the bundled registry", () => {
  it("is the eleven-entry contract fixture, parsed once", () => {
    const entries = builtinExtractors();
    expect(entries).toHaveLength(11);
    expect(new Set(entries.map((e) => e.id)).size).toBe(11);
    expect(builtinExtractors()).toBe(entries);
    expect(findBuiltinExtractor("stactools-goes")?.adapter).toBe("goes");
    expect(findBuiltinExtractor("stactools-nope")).toBeNull();
  });
});

describe("the process template", () => {
  it("is the two-line body on the stactools image, and passes the write gate", () => {
    const entry = findBuiltinExtractor("stactools-sentinel2")!;
    const template = builtinRevisionTemplate(entry);
    expect(template.code).toBe(
      'from stac_higher_stactools import run\nrun("stactools-sentinel2")\n',
    );
    expect(template.code).toBe(builtinProcessBody(entry));
    expect(template.runtime).toEqual({
      kind: "inline_python",
      image: null,
      memory_mb: 4096,
      timeout_seconds: 600,
      retry: { max_attempts: 3, backoff: "exponential" },
      network: { level: "isolated", hosts: [] },
      runtime_image: "stactools",
    });
    expect(template.env).toEqual([]);
    expect(processRevisionCreateSchema.safeParse(template).success).toBe(true);
  });

  it("names and paces the process like an extractor (spec §7)", () => {
    const defaults = builtinProcessDefaults(findBuiltinExtractor("stactools-goes")!);
    expect(defaults.name).toBe("GOES-R ABI (L1b / L2)");
    expect(defaults.maxRunsPerHour).toBe(600);
    expect(defaults.description).toContain("stactools-goes 0.1.8");
  });

  it("every registry entry yields a deployable template", () => {
    for (const entry of builtinExtractors()) {
      expect(builtinRevisionTemplate(entry).runtime.runtime_image).toBe("stactools");
    }
  });
});

describe("the picker's grouping filter (spec §3.6)", () => {
  it("offers grouped packages to grouped associations and single-file ones otherwise", () => {
    expect(builtinSupportsGrouping("single_file", "none")).toBe(true);
    expect(builtinSupportsGrouping("single_file", "shared_basename")).toBe(false);
    expect(builtinSupportsGrouping("grouped", "shared_basename")).toBe(true);
    expect(builtinSupportsGrouping("grouped", "none")).toBe(false);
  });
});

describe("the guard", () => {
  it("audits create-or-reuse as a process create", () => {
    expect(matchGatedRoute("POST", "/api/processes/builtin")).toEqual({
      action: "create",
      resourceType: "process",
      resourceId: null,
    });
    expect(matchGatedRoute("GET", "/api/extractors/builtin")).toBeNull();
  });
});
