/**
 * Credential envelope encryption (ROADMAP §5.2 + Phase 2 contract).
 *
 * Envelope wire format (bytea, shared with the Python pipeline):
 *
 *   byte 0        version (0x01)
 *   bytes 1..12   random 96-bit nonce
 *   bytes 13..    AES-256-GCM ciphertext || 16-byte auth tag
 *
 * The plaintext is the UTF-8 JSON encoding of the per-protocol credential
 * object (lib/connections/schemas.ts).
 *
 * Provider seam: the app encrypts on create/update and NEVER decrypts —
 * credentials are write-only through the API, and decryption happens only
 * inside the pipeline service at job execution time. `decrypt` exists on the
 * interface because every real provider (local key, KMS in Phase 8) can
 * round-trip, and tests prove the envelope against tampering — but no app
 * request path may call it.
 *
 * Key: env CREDENTIALS_MASTER_KEY, 32 random bytes as standard base64.
 * Generate one with `openssl rand -base64 32`, or with Node:
 *
 *   node -e "console.log(require('node:crypto').randomBytes(32).toString('base64'))"
 *
 * Never use base64url: the app accepts it but the pipeline rejects it.
 *
 * Put it in the root `.env`. Compose passes it to the pipeline; the dev
 * server reads process.env, so start it with the file sourced:
 *
 *   cd app && set -a && source ../.env && set +a && npm run dev
 *
 * See docs/connections.md, "The master key". Missing/malformed keys fail
 * loudly at write time — there is no silent fallback key.
 */
import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
} from "node:crypto";

export const ENVELOPE_VERSION = 0x01;
const NONCE_LENGTH = 12;
const TAG_LENGTH = 16;
const KEY_LENGTH = 32;

export interface EncryptionProvider {
  /** Seal a UTF-8 plaintext into a versioned envelope. */
  encrypt(plaintext: string): Buffer;
  /**
   * Open an envelope. Pipeline-side operation — no app request path may call
   * this; it exists so providers are testable and the seam matches Phase 8
   * KMS envelope encryption.
   */
  decrypt(envelope: Buffer): string;
}

export class CredentialKeyError extends Error {}
export class EnvelopeError extends Error {}

/** Decode + validate CREDENTIALS_MASTER_KEY. Throws loudly when unusable. */
export function loadMasterKey(
  env: Record<string, string | undefined> = process.env,
): Buffer {
  const raw = env.CREDENTIALS_MASTER_KEY;
  if (!raw) {
    throw new CredentialKeyError(
      "CREDENTIALS_MASTER_KEY is not set. Credentials cannot be stored without it. " +
        "Generate one with `openssl rand -base64 32` (standard base64, not base64url), " +
        "put it in the root .env, and restart the dev server with that file sourced. " +
        'See docs/connections.md, "The master key".',
    );
  }
  const key = Buffer.from(raw, "base64");
  if (key.length !== KEY_LENGTH) {
    throw new CredentialKeyError(
      `CREDENTIALS_MASTER_KEY must be base64-encoded ${KEY_LENGTH} bytes (got ${key.length} bytes after decoding).`,
    );
  }
  return key;
}

/** Pure envelope seal — exported for tests and provider implementations. */
export function sealEnvelope(key: Buffer, plaintext: string): Buffer {
  const nonce = randomBytes(NONCE_LENGTH);
  const cipher = createCipheriv("aes-256-gcm", key, nonce);
  const ciphertext = Buffer.concat([
    cipher.update(plaintext, "utf8"),
    cipher.final(),
  ]);
  return Buffer.concat([
    Buffer.from([ENVELOPE_VERSION]),
    nonce,
    ciphertext,
    cipher.getAuthTag(),
  ]);
}

/** Pure envelope open — pipeline analog lives in Python; here for tests. */
export function openEnvelope(key: Buffer, envelope: Buffer): string {
  if (envelope.length < 1 + NONCE_LENGTH + TAG_LENGTH) {
    throw new EnvelopeError("Credential envelope is truncated");
  }
  if (envelope[0] !== ENVELOPE_VERSION) {
    throw new EnvelopeError(
      `Unsupported credential envelope version 0x${envelope[0].toString(16).padStart(2, "0")}`,
    );
  }
  const nonce = envelope.subarray(1, 1 + NONCE_LENGTH);
  const tag = envelope.subarray(envelope.length - TAG_LENGTH);
  const ciphertext = envelope.subarray(
    1 + NONCE_LENGTH,
    envelope.length - TAG_LENGTH,
  );
  const decipher = createDecipheriv("aes-256-gcm", key, nonce);
  decipher.setAuthTag(tag);
  try {
    return Buffer.concat([
      decipher.update(ciphertext),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    throw new EnvelopeError(
      "Credential envelope failed authentication (wrong key or tampered data)",
    );
  }
}

/** Local master-key provider (default; KMS provider arrives in Phase 8). */
export class LocalKeyEncryptionProvider implements EncryptionProvider {
  private key: Buffer;

  constructor(key?: Buffer) {
    this.key = key ?? loadMasterKey();
  }

  encrypt(plaintext: string): Buffer {
    return sealEnvelope(this.key, plaintext);
  }

  decrypt(envelope: Buffer): string {
    return openEnvelope(this.key, envelope);
  }
}

/**
 * Provider factory. Local master key is the only implementation today;
 * Phase 8 adds a KMS-backed provider selected per deployment. Constructed
 * lazily (per write) so a missing key fails the credential write with a
 * clear error instead of crashing the server at import time.
 */
export function getEncryptionProvider(): EncryptionProvider {
  return new LocalKeyEncryptionProvider();
}
