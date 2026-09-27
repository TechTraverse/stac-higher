// @vitest-environment node
/**
 * `processRevisionCreateSchema` (C-1, container-images spec §3): code is
 * required for inline_python and inline_python_on_image, and refused for
 * container, where the image is the process.
 */
import { describe, expect, it } from "vitest";
import { processRevisionCreateSchema } from "@/lib/processes/schemas";

const SNAP = {
  id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
  reference: "ghcr.io/example/satpy-runtime",
  digest: "sha256:" + "a".repeat(64),
};

function revision(runtime: Record<string, unknown>, code: string | null) {
  return processRevisionCreateSchema.safeParse({ runtime, code, env: [] });
}

describe("processRevisionCreateSchema — code by kind", () => {
  it("kind 2 carries code, like kind 1", () => {
    expect(revision({ kind: "inline_python_on_image", image: SNAP }, "print(1)").success).toBe(true);
    const missing = revision({ kind: "inline_python_on_image", image: SNAP }, null);
    expect(missing.success).toBe(false);
    expect(JSON.stringify(missing.error?.issues)).toContain("inline_python_on_image revisions need code");
  });

  it("kind 3 refuses code: the image is the process", () => {
    expect(revision({ kind: "container", image: SNAP }, null).success).toBe(true);
    const withCode = revision({ kind: "container", image: SNAP }, "print(1)");
    expect(withCode.success).toBe(false);
    expect(JSON.stringify(withCode.error?.issues)).toContain("container revisions carry no code");
  });

  it("kind 3 refuses an empty-string code too (only null means 'no code')", () => {
    expect(revision({ kind: "container", image: SNAP }, "").success).toBe(false);
  });

  it("kind 1 is unchanged", () => {
    expect(revision({ kind: "inline_python" }, "print(1)").success).toBe(true);
    expect(revision({ kind: "inline_python" }, "   ").success).toBe(false);
  });
});
