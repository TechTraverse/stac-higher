/**
 * Object-storage configuration (ROADMAP Phase 3 — asset service).
 *
 * Read server-only via `process.env` behind an injectable-`env` function, the
 * same testability pattern as `loadMasterKey` / the auth config. Nothing here
 * is `PUBLIC_*` — the app signs URLs server-side and never ships credentials to
 * the browser.
 *
 * Presigning is an OFFLINE operation (HMAC over the request), so the endpoint
 * only has to be reachable by whoever *uses* the URL — the browser. In local
 * dev the app runs on the host, so `http://localhost:9000` works for both. When
 * the app runs inside compose, set `S3_ENDPOINT` to a browser-reachable host
 * (never the compose-internal `http://minio:9000`, which the browser can't
 * resolve).
 *
 * Credentials: when both `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` are
 * unset or blank, `credentials` is `null` and the AWS SDK default credential
 * chain runs (Pod Identity / IRSA on EKS, env, profile). The one exception is
 * a bare laptop with no `S3_*` set at all: the compose MinIO defaults apply, so
 * a developer's personal AWS credentials never sign MinIO URLs. Exactly one key
 * set is a configuration error.
 */

export interface StorageCredentials {
  accessKeyId: string;
  secretAccessKey: string;
}

export interface StorageConfig {
  endpoint: string;
  region: string;
  bucket: string;
  /** null = the AWS SDK default credential chain (Pod Identity / IRSA / env / profile). */
  credentials: StorageCredentials | null;
  /** MinIO needs path-style addressing (no per-bucket DNS). */
  forcePathStyle: boolean;
}

// Local MinIO defaults from docker-compose (minioadmin/minioadmin, bucket
// `stac-higher`). Overridden by env in any real deployment.
const DEFAULT_ENDPOINT = "http://localhost:9000";
const DEFAULT_REGION = "us-east-1";
const DEFAULT_BUCKET = "stac-higher";
const DEFAULT_ACCESS_KEY = "minioadmin";
const DEFAULT_SECRET_KEY = "minioadmin";

const isEmpty = (v: string | undefined): v is undefined => v === undefined || v.trim() === "";

function resolveCredentials(env: Record<string, string | undefined>): StorageCredentials | null {
  const id = env.S3_ACCESS_KEY_ID;
  const secret = env.S3_SECRET_ACCESS_KEY;
  if (isEmpty(id) && isEmpty(secret)) {
    // Dev-only MinIO default: only when nothing S3-related is set at all.
    const bare = env.S3_ENDPOINT === undefined && id === undefined && secret === undefined;
    return bare ? { accessKeyId: DEFAULT_ACCESS_KEY, secretAccessKey: DEFAULT_SECRET_KEY } : null;
  }
  if (isEmpty(id) || isEmpty(secret)) {
    throw new Error(
      "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be set together, or both left empty to use the AWS default credential chain",
    );
  }
  return { accessKeyId: id as string, secretAccessKey: secret as string };
}

function parseBool(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  return value === "1" || value.toLowerCase() === "true";
}

export function getStorageConfig(
  env: Record<string, string | undefined> = process.env,
): StorageConfig {
  return {
    endpoint: env.S3_ENDPOINT ?? DEFAULT_ENDPOINT,
    region: env.S3_REGION ?? DEFAULT_REGION,
    bucket: env.S3_BUCKET ?? DEFAULT_BUCKET,
    credentials: resolveCredentials(env),
    // MinIO is the local default and requires path-style; keep it on unless a
    // real-S3 deployment explicitly turns it off.
    forcePathStyle: parseBool(env.S3_FORCE_PATH_STYLE, true),
  };
}
