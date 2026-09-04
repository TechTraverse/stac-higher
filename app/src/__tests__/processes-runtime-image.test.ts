import { describe, it, expect } from "vitest";
import {
  PROCESS_RUNTIME_IMAGE_ALIASES,
  processRuntimeReadSchema,
  processRuntimeSchema,
} from "@/lib/processes/schemas";

/**
 * `runtime.runtime_image` (X-queue spec §8): an ALIAS of a platform-built
 * image, resolved by the pipeline at launch. It is not `runtime.image` — that
 * is a user-supplied reference the write gate refuses (ADR 0013) — and it
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

  it("does not open the container arm: an alias beside a user image is still refused", () => {
    const doc = { kind: "container", image: "ghcr.io/x/y:1", runtime_image: "stactools" };
    expect(processRuntimeReadSchema.safeParse(doc).success).toBe(true);
    expect(processRuntimeSchema.safeParse(doc).success).toBe(false);
  });
});
