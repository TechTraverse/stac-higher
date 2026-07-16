/**
 * S3 client factory for the asset service (ROADMAP Phase 3).
 *
 * The app only ever *presigns* — it hands short-lived URLs to the browser and
 * never streams bytes itself — so this client makes no network calls in the
 * request path. `defaultClient()` memoizes one built from the process env.
 */
import { S3Client } from "@aws-sdk/client-s3";
import { getStorageConfig, type StorageConfig } from "./config";

export function buildClient(config: StorageConfig): S3Client {
  return new S3Client({
    endpoint: config.endpoint,
    region: config.region,
    forcePathStyle: config.forcePathStyle,
    credentials: {
      accessKeyId: config.accessKeyId,
      secretAccessKey: config.secretAccessKey,
    },
  });
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
