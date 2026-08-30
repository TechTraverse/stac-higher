// @vitest-environment node
// (pure key-layout + input hardening — no DOM, no network)
import { describe, it, expect } from "vitest";
import {
  canonicalAssetKey,
  stagingKey,
  assetHref,
  sanitizeFilename,
  assertSafeSegment,
  stagedHref,
  parseStagedHref,
  isStagedHref,
  StorageKeyError,
} from "@/lib/storage/keys";

describe("canonicalAssetKey (§5.3 layout)", () => {
  it("builds assets/{collection}/{item}/{filename}", () => {
    expect(canonicalAssetKey("sentinel-2", "S2A_001", "B04.tif")).toBe(
      "assets/sentinel-2/S2A_001/B04.tif",
    );
  });

  it("sanitizes an unsafe filename but keeps the extension", () => {
    expect(canonicalAssetKey("c", "i", "my file (1).tif")).toBe(
      "assets/c/i/my_file__1_.tif",
    );
  });

  it("strips any directory portion a client sends in the filename", () => {
    expect(canonicalAssetKey("c", "i", "../../etc/passwd")).toBe("assets/c/i/passwd");
    expect(canonicalAssetKey("c", "i", "sub/dir/x.tif")).toBe("assets/c/i/x.tif");
  });

  it("rejects a collection or item that is a path-traversal segment", () => {
    expect(() => canonicalAssetKey("..", "i", "x.tif")).toThrow(StorageKeyError);
    expect(() => canonicalAssetKey("c", "a/b", "x.tif")).toThrow(StorageKeyError);
    expect(() => canonicalAssetKey("c", "", "x.tif")).toThrow(StorageKeyError);
  });

  it("rejects a filename that sanitizes to nothing usable", () => {
    expect(() => canonicalAssetKey("c", "i", "..")).toThrow(StorageKeyError);
    expect(() => canonicalAssetKey("c", "i", "/")).toThrow(StorageKeyError);
  });
});

describe("stagingKey (Phase 7 seam)", () => {
  it("builds staging/{upload_id}/{filename}", () => {
    expect(stagingKey("abc-123", "scene.tif")).toBe("staging/abc-123/scene.tif");
  });
  it("rejects an unsafe upload id", () => {
    expect(() => stagingKey("../x", "a.tif")).toThrow(StorageKeyError);
  });
});

describe("staged hrefs (Phase 7 §4.2 grammar — full case table lives in the golden fixture)", () => {
  const UPLOAD_ID = "0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060";

  it("stagedHref mints staging://{upload_id}/{filename} with the filename sanitized", () => {
    expect(stagedHref(UPLOAD_ID, "my file (1).tif")).toBe(
      `staging://${UPLOAD_ID}/my_file__1_.tif`,
    );
  });

  it("stagedHref rejects an unsafe upload id", () => {
    expect(() => stagedHref("../x", "a.tif")).toThrow(StorageKeyError);
  });

  it("stagedHref names exactly the object stagingKey addresses", () => {
    const href = stagedHref(UPLOAD_ID, "B04.tif");
    const { uploadId, filename } = parseStagedHref(href);
    expect(stagingKey(uploadId, filename)).toBe(`staging/${UPLOAD_ID}/B04.tif`);
  });

  it("parseStagedHref round-trips a minted href", () => {
    expect(parseStagedHref(stagedHref(UPLOAD_ID, "B04.tif"))).toEqual({
      uploadId: UPLOAD_ID,
      filename: "B04.tif",
    });
  });

  it("parseStagedHref rejects what a mint could never produce", () => {
    expect(() => parseStagedHref("https://x/B04.tif")).toThrow(StorageKeyError);
    expect(() => parseStagedHref(`staging://${UPLOAD_ID}`)).toThrow(StorageKeyError);
    expect(() => parseStagedHref(`staging://${UPLOAD_ID}/a/../b.tif`)).toThrow(
      StorageKeyError,
    );
  });

  it("isStagedHref is a plain case-sensitive prefix check", () => {
    expect(isStagedHref(`staging://${UPLOAD_ID}/B04.tif`)).toBe(true);
    expect(isStagedHref("/api/assets/c/i/B04.tif")).toBe(false);
    expect(isStagedHref(42)).toBe(false);
  });
});

describe("assetHref", () => {
  it("points at the asset route with the (sanitized) filename", () => {
    expect(assetHref("sentinel-2", "S2A_001", "B04.tif")).toBe(
      "/api/assets/sentinel-2/S2A_001/B04.tif",
    );
  });
  it("url-encodes segments", () => {
    expect(assetHref("a b", "i", "x.tif")).toBe("/api/assets/a%20b/i/x.tif");
  });
});

describe("primitives", () => {
  it("assertSafeSegment returns the value when safe", () => {
    expect(assertSafeSegment("ok_name-1.2", "seg")).toBe("ok_name-1.2");
  });
  it("sanitizeFilename replaces disallowed chars with underscore", () => {
    expect(sanitizeFilename("a@b#c.tif")).toBe("a_b_c.tif");
  });
});
