// @vitest-environment node
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/storage/reference", () => ({ lookupReferenceHref: vi.fn() }));
vi.mock("@/lib/storage/presign", () => ({ presignGetUrl: vi.fn(async () => "http://canonical/presigned") }));

import { lookupReferenceHref } from "@/lib/storage/reference";
import { presignGetUrl } from "@/lib/storage/presign";
import { resolveAssetTarget } from "@/lib/storage/resolve";

const mockLookup = vi.mocked(lookupReferenceHref);
beforeEach(() => { mockLookup.mockReset(); vi.mocked(presignGetUrl).mockClear(); });

describe("resolveAssetTarget", () => {
  it("302 target is the source href in reference mode", async () => {
    mockLookup.mockResolvedValueOnce("http://src/scene.tif");
    const t = await resolveAssetTarget("col", "scene", "scene.tif");
    expect(t).toEqual({ url: "http://src/scene.tif", mode: "reference" });
    expect(presignGetUrl).not.toHaveBeenCalled();
  });

  it("falls back to presigned canonical when not referenced", async () => {
    mockLookup.mockResolvedValueOnce(null);
    const t = await resolveAssetTarget("col", "scene", "scene.tif");
    expect(t.mode).toBe("canonical");
    expect(t.url).toBe("http://canonical/presigned");
  });
});
