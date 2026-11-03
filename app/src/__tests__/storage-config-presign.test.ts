// @vitest-environment node
// (config parsing + offline HMAC presigning — no network)
import { describe, it, expect, vi, afterEach } from "vitest";
import { getStorageConfig, type StorageConfig } from "@/lib/storage/config";
import { s3ClientConfig } from "@/lib/storage/client";
import { presignGetUrl, presignPutUrl } from "@/lib/storage/presign";

afterEach(() => vi.unstubAllEnvs());

describe("getStorageConfig", () => {
  it("defaults to the local MinIO compose settings when nothing is set", () => {
    expect(getStorageConfig({})).toEqual({
      endpoint: "http://localhost:9000", region: "us-east-1", bucket: "stac-higher",
      credentials: { accessKeyId: "minioadmin", secretAccessKey: "minioadmin" }, forcePathStyle: true,
    });
  });
  it("uses the default chain when an endpoint is configured without keys", () => {
    expect(getStorageConfig({ S3_ENDPOINT: "https://s3.us-east-1.amazonaws.com" }).credentials).toBeNull();
  });
  it("uses the default chain when both keys are explicitly empty, even with no endpoint", () => {
    expect(getStorageConfig({ S3_ACCESS_KEY_ID: "", S3_SECRET_ACCESS_KEY: " " }).credentials).toBeNull();
  });
  it("keeps explicit static keys", () => {
    expect(getStorageConfig({ S3_ACCESS_KEY_ID: "AKIA", S3_SECRET_ACCESS_KEY: "shhh" }).credentials)
      .toEqual({ accessKeyId: "AKIA", secretAccessKey: "shhh" });
  });
  it("refuses exactly one key (Review Focus 2)", () => {
    expect(() => getStorageConfig({ S3_ACCESS_KEY_ID: "AKIA" })).toThrow(/S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY/);
    expect(() => getStorageConfig({ S3_ENDPOINT: "https://s3.example.org", S3_SECRET_ACCESS_KEY: "shhh" })).toThrow(/S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY/);
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
  credentials: { accessKeyId: "minioadmin", secretAccessKey: "minioadmin" },
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

  it("omits credentials from the S3 client config for the default chain", () => {
    expect("credentials" in s3ClientConfig({ ...TEST_CONFIG, credentials: null })).toBe(false);
    expect(s3ClientConfig(TEST_CONFIG).credentials).toEqual(TEST_CONFIG.credentials);
  });

  it("presigns with whatever the default chain resolves (env provider here)", async () => {
    vi.stubEnv("AWS_PROFILE", ""); // a developer's profile would outrank the env provider
    vi.stubEnv("AWS_ACCESS_KEY_ID", "AKIDEFAULTCHAIN");
    vi.stubEnv("AWS_SECRET_ACCESS_KEY", "default-chain-secret");
    const url = await presignGetUrl("assets/c/i/x.tif", { config: { ...TEST_CONFIG, credentials: null } });
    expect(url).toContain("X-Amz-Credential=AKIDEFAULTCHAIN");
  });
});
