/**
 * S3 client factory for the asset service (ROADMAP Phase 3).
 *
 * The app only ever *presigns* — it hands short-lived URLs to the browser and
 * never streams bytes itself — so this client makes no network calls in the
 * request path. `defaultClient()` memoizes one built from the process env.
 *
 * With `credentials: null` the SDK default chain resolves (and refreshes) Pod
 * Identity / IRSA credentials itself; the 300 s / 900 s presign TTLs sit well
 * inside a session.
 */
import { S3Client, type S3ClientConfig } from "@aws-sdk/client-s3";
import { getStorageConfig, type StorageConfig } from "./config";

export function s3ClientConfig(config: StorageConfig): S3ClientConfig {
  return {
    endpoint: config.endpoint,
    region: config.region,
    forcePathStyle: config.forcePathStyle,
    ...(config.credentials ? { credentials: config.credentials } : {}),
  };
}

export function buildClient(config: StorageConfig): S3Client {
  return new S3Client(s3ClientConfig(config));
}

let cached: { client: S3Client; config: StorageConfig } | null = null;

/** Process-wide client + config, built once from env. */
export function defaultStorage(): { client: S3Client; config: StorageConfig } {
  if (!cached) {
    const config = getStorageConfig();
    cached = { client: buildClient(config), config };
  }
  return cached;
}
