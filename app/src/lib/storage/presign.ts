/**
 * Presigned URL minting (ROADMAP Phase 3).
 *
 * GET → the asset service redirects (302) a caller to a short-lived download
 * URL. PUT → the upload endpoint hands the browser a short-lived upload URL so
 * bytes go straight to object storage, never through the app.
 *
 * Signing is offline (HMAC), so these never touch the network. `config` is
 * injectable for tests; production calls fall back to `defaultStorage()`.
 */
import { GetObjectCommand, PutObjectCommand, type S3Client } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { buildClient, defaultStorage } from "./client";
import type { StorageConfig } from "./config";

// Short lifetimes: a download link only has to survive the redirect; an upload
// link only has to survive the browser PUT that follows immediately.
export const GET_TTL_SECONDS = 300; // 5 min
export const PUT_TTL_SECONDS = 900; // 15 min

interface PresignOptions {
  config?: StorageConfig;
  expiresIn?: number;
}

function resolve(opts?: PresignOptions): { client: S3Client; config: StorageConfig } {
  return opts?.config
    ? { client: buildClient(opts.config), config: opts.config }
    : defaultStorage();
}

/** Presigned GET URL for a canonical object key. */
export function presignGetUrl(key: string, opts?: PresignOptions): Promise<string> {
  const { client, config } = resolve(opts);
  return getSignedUrl(client, new GetObjectCommand({ Bucket: config.bucket, Key: key }), {
    expiresIn: opts?.expiresIn ?? GET_TTL_SECONDS,
  });
}

/** Presigned PUT URL for an object key (browser uploads the bytes). */
export function presignPutUrl(
  key: string,
  contentType?: string,
  opts?: PresignOptions,
): Promise<string> {
  const { client, config } = resolve(opts);
  return getSignedUrl(
    client,
    new PutObjectCommand({ Bucket: config.bucket, Key: key, ContentType: contentType }),
    { expiresIn: opts?.expiresIn ?? PUT_TTL_SECONDS },
  );
}
