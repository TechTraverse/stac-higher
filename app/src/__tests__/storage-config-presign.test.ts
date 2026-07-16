// @vitest-environment node
// (config parsing + offline HMAC presigning — no network)
import { describe, it, expect } from "vitest";
import { getStorageConfig, type StorageConfig } from "@/lib/storage/config";
import { presignGetUrl, presignPutUrl } from "@/lib/storage/presign";

describe("getStorageConfig", () => {
  it("defaults to the local MinIO compose settings", () => {
    const c = getStorageConfig({});
    expect(c).toEqual({
      endpoint: "http://localhost:9000",
      region: "us-east-1",
      bucket: "stac-higher",
      accessKeyId: "minioadmin",
      secretAccessKey: "minioadmin",
      forcePathStyle: true,
    });
  });

  it("reads overrides from env and parses forcePathStyle", () => {
    const c = getStorageConfig({
      S3_ENDPOINT: "https://s3.amazonaws.com",
      S3_REGION: "us-gov-west-1",
      S3_BUCKET: "prod-assets",
      S3_ACCESS_KEY_ID: "AKIA",
      S3_SECRET_ACCESS_KEY: "shhh",
      S3_FORCE_PATH_STYLE: "false",
    });
    expect(c.endpoint).toBe("https://s3.amazonaws.com");
    expect(c.region).toBe("us-gov-west-1");
    expect(c.bucket).toBe("prod-assets");
    expect(c.forcePathStyle).toBe(false);
  });
});

const TEST_CONFIG: StorageConfig = {
  endpoint: "http://localhost:9000",
  region: "us-east-1",
  bucket: "stac-higher",
  accessKeyId: "minioadmin",
  secretAccessKey: "minioadmin",
  forcePathStyle: true,
};

describe("presign (offline)", () => {
  it("presignGetUrl signs a path-style URL for the canonical key", async () => {
    const url = await presignGetUrl("assets/c/i/x.tif", { config: TEST_CONFIG });
    expect(url).toContain("http://localhost:9000/stac-higher/assets/c/i/x.tif");
    expect(url).toContain("X-Amz-Signature=");
    expect(url).toContain("X-Amz-Expires=300");
  });

  it("presignPutUrl signs a PUT with a longer TTL", async () => {
    const url = await presignPutUrl("assets/c/i/x.tif", "image/tiff", {
      config: TEST_CONFIG,
    });
    expect(url).toContain("/stac-higher/assets/c/i/x.tif");
    expect(url).toContain("X-Amz-Signature=");
    expect(url).toContain("X-Amz-Expires=900");
  });
});
