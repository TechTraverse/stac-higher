/**
 * Session cookie: an encrypted+authenticated JWE (jose, dir/A256GCM) holding
 * the OIDC tokens. httpOnly + SameSite=Lax; `secure` when the app runs on
 * https. Keycloak's three tokens can exceed the ~4 KB per-cookie limit, so
 * the sealed value is split across `sh_session`, `sh_session.1`, … chunks.
 *
 * Identity is NOT stored in the cookie — it is derived per request from the
 * access token (claims-mapping layer), so role/group changes propagate on
 * the next token refresh instead of living for the whole session.
 */
import { createHash } from "node:crypto";
import { EncryptJWT, jwtDecrypt } from "jose";

export const SESSION_COOKIE = "sh_session";
export const TXN_COOKIE = "sh_oidc_txn";
/** Keep chunks comfortably under the 4096-byte browser cookie limit
 * (name + attributes count against it too). */
export const COOKIE_CHUNK_SIZE = 3500;
const MAX_CHUNKS = 8;

export interface SessionData {
  accessToken: string;
  refreshToken: string | null;
  /** Kept only for the `id_token_hint` on RP-initiated logout. */
  idToken: string | null;
  /** Access-token expiry, epoch seconds. */
  expiresAt: number;
}

/** Minimal cookie surface — structurally satisfied by Astro's `context.cookies`
 * and trivially stubbed in unit tests. */
export interface CookieJar {
  get(name: string): { value: string } | undefined;
  set(name: string, value: string, opts?: CookieSetOptions): void;
  delete(name: string, opts?: { path?: string }): void;
}

export interface CookieSetOptions {
  httpOnly?: boolean;
  secure?: boolean;
  sameSite?: "lax" | "strict" | "none";
  path?: string;
  maxAge?: number;
}

function deriveKey(secret: string): Uint8Array {
  return new Uint8Array(createHash("sha256").update(secret).digest());
}

/** Seal an arbitrary payload into a compact encrypted token with an absolute
 * expiry `maxAgeS` seconds from now. */
export async function sealValue(
  payload: Record<string, unknown>,
  secret: string,
  maxAgeS: number,
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  return new EncryptJWT(payload)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt(now)
    .setExpirationTime(now + maxAgeS)
    .encrypt(deriveKey(secret));
}

/** Open a sealed value. Returns `null` on any failure (tampered, wrong
 * secret, expired) — callers treat that as "no session". */
export async function openValue<T>(
  token: string,
  secret: string,
): Promise<T | null> {
  try {
    const { payload } = await jwtDecrypt(token, deriveKey(secret));
    return payload as T;
  } catch {
    return null;
  }
}

export function chunkValue(
  value: string,
  size: number = COOKIE_CHUNK_SIZE,
): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < value.length; i += size) {
    chunks.push(value.slice(i, i + size));
  }
  return chunks.length > 0 ? chunks : [""];
}

function chunkName(name: string, index: number): string {
  return index === 0 ? name : `${name}.${index}`;
}

export function writeChunkedCookie(
  cookies: CookieJar,
  name: string,
  value: string,
  opts: CookieSetOptions,
): void {
  const chunks = chunkValue(value);
  if (chunks.length > MAX_CHUNKS) {
    throw new Error(`Cookie ${name} exceeds ${MAX_CHUNKS} chunks`);
  }
  chunks.forEach((chunk, i) => {
    cookies.set(chunkName(name, i), chunk, opts);
  });
  // Remove stale higher-index chunks from a previously larger session.
  for (let i = chunks.length; i < MAX_CHUNKS; i++) {
    if (cookies.get(chunkName(name, i)) !== undefined) {
      cookies.delete(chunkName(name, i), { path: opts.path ?? "/" });
    }
  }
}

export function readChunkedCookie(
  cookies: CookieJar,
  name: string,
): string | null {
  const first = cookies.get(name)?.value;
  if (first === undefined) return null;
  let value = first;
  for (let i = 1; i < MAX_CHUNKS; i++) {
    const chunk = cookies.get(chunkName(name, i))?.value;
    if (chunk === undefined) break;
    value += chunk;
  }
  return value;
}

export function clearChunkedCookie(cookies: CookieJar, name: string): void {
  for (let i = 0; i < MAX_CHUNKS; i++) {
    if (i === 0 || cookies.get(chunkName(name, i)) !== undefined) {
      cookies.delete(chunkName(name, i), { path: "/" });
    }
  }
}

export function sessionCookieOptions(opts: {
  secure: boolean;
  maxAgeS: number;
}): CookieSetOptions {
  return {
    httpOnly: true,
    sameSite: "lax",
    secure: opts.secure,
    path: "/",
    maxAge: opts.maxAgeS,
  };
}

/** `secure` cookies only when the app itself is served over https. */
export function isSecureOrigin(redirectUri: string): boolean {
  try {
    return new URL(redirectUri).protocol === "https:";
  } catch {
    return false;
  }
}

export async function writeSession(
  cookies: CookieJar,
  session: SessionData,
  cfg: { sessionSecret: string; sessionMaxAgeS: number; redirectUri: string },
): Promise<void> {
  const sealed = await sealValue(
    { sess: session },
    cfg.sessionSecret,
    cfg.sessionMaxAgeS,
  );
  writeChunkedCookie(
    cookies,
    SESSION_COOKIE,
    sealed,
    sessionCookieOptions({
      secure: isSecureOrigin(cfg.redirectUri),
      maxAgeS: cfg.sessionMaxAgeS,
    }),
  );
}

export async function readSession(
  cookies: CookieJar,
  secret: string,
): Promise<SessionData | null> {
  const sealed = readChunkedCookie(cookies, SESSION_COOKIE);
  if (!sealed) return null;
  const payload = await openValue<{ sess?: SessionData }>(sealed, secret);
  const sess = payload?.sess;
  if (
    !sess ||
    typeof sess.accessToken !== "string" ||
    typeof sess.expiresAt !== "number"
  ) {
    return null;
  }
  return sess;
}

export function clearSession(cookies: CookieJar): void {
  clearChunkedCookie(cookies, SESSION_COOKIE);
}
