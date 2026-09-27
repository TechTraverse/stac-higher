import { describe, it, expect } from "vitest";
import {
  PROCESS_RUNTIME_IMAGE_ALIASES,
  processRuntimeReadSchema,
  processRuntimeSchema,
} from "@/lib/processes/schemas";

/**
 * `runtime.runtime_image` (X-queue spec §8): an ALIAS of a platform-built
 * image, resolved by the pipeline at launch. It is not `runtime.image`, which
 * is the scanned user-image snapshot of kinds 2–3 (C-1, ADR 0021), and it
 * defaults to `default` because every stored revision predates it.
 */

describe("runtime.runtime_image", () => {
  it("defaults to `default` and leaves `image` null", () => {
    const rt = processRuntimeSchema.parse({ kind: "inline_python" });
    expect(rt.runtime_image).toBe("default");
    expect(rt.image).toBeNull();
  });

  it("accepts every platform alias on both the read and the write schema", () => {
    for (const alias of PROCESS_RUNTIME_IMAGE_ALIASES) {
      const doc = { kind: "inline_python", runtime_image: alias };
      expect(processRuntimeReadSchema.safeParse(doc).success).toBe(true);
      expect(processRuntimeSchema.parse(doc).runtime_image).toBe(alias);
    }
    expect(PROCESS_RUNTIME_IMAGE_ALIASES).toEqual(["default", "stactools"]);
  });

  it("refuses an alias that names no platform image, and a literal reference", () => {
    for (const runtime_image of ["cuda", "ghcr.io/example/proc:1", "", null]) {
      expect(
        processRuntimeReadSchema.safeParse({ kind: "inline_python", runtime_image }).success,
      ).toBe(false);
    }
  });

  it("an alias beside a user image is refused on both schemas (spec §3: runtime_image is null for kinds 2–3)", () => {
    const image = {
      id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
      reference: "ghcr.io/x/y",
      digest: "sha256:" + "a".repeat(64),
    };
    for (const kind of ["inline_python_on_image", "container"]) {
      const doc = { kind, image, runtime_image: "stactools" };
      expect(processRuntimeReadSchema.safeParse(doc).success, kind).toBe(false);
      expect(processRuntimeSchema.safeParse(doc).success, kind).toBe(false);
      expect(processRuntimeSchema.parse({ kind, image }).runtime_image, kind).toBeNull();
    }
  });
});
