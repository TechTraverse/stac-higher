// @vitest-environment node
// (server-side crypto — no DOM involved)
import { describe, it, expect } from "vitest";
import { randomBytes } from "node:crypto";
import {
  CredentialKeyError,
  ENVELOPE_VERSION,
  EnvelopeError,
  LocalKeyEncryptionProvider,
  loadMasterKey,
  openEnvelope,
  sealEnvelope,
} from "@/lib/connections/crypto";

const key = randomBytes(32);
const plaintext = JSON.stringify({
  username: "ingest",
  password: "hunter2-super-secret",
});

describe("credential envelope", () => {
  it("round-trips UTF-8 credential JSON", () => {
    const envelope = sealEnvelope(key, plaintext);
    expect(openEnvelope(key, envelope)).toBe(plaintext);
  });

  it("matches the contract wire format: version || nonce || ct+tag", () => {
    const envelope = sealEnvelope(key, plaintext);
    expect(envelope[0]).toBe(ENVELOPE_VERSION);
    expect(envelope[0]).toBe(0x01);
    // 1 version + 12 nonce + ciphertext (= plaintext length for GCM) + 16 tag
    expect(envelope.length).toBe(1 + 12 + Buffer.byteLength(plaintext) + 16);
  });

  it("uses a fresh nonce per seal (same plaintext, different envelopes)", () => {
    const a = sealEnvelope(key, plaintext);
    const b = sealEnvelope(key, plaintext);
    expect(a.equals(b)).toBe(false);
    expect(a.subarray(1, 13).equals(b.subarray(1, 13))).toBe(false);
  });

  it("never leaks the plaintext into the envelope", () => {
    const envelope = sealEnvelope(key, plaintext);
    expect(envelope.includes(Buffer.from("hunter2"))).toBe(false);
  });

  it("rejects a tampered ciphertext byte", () => {
    const envelope = sealEnvelope(key, plaintext);
    envelope[14] ^= 0xff; // first ciphertext byte
    expect(() => openEnvelope(key, envelope)).toThrow(EnvelopeError);
  });

  it("rejects a tampered auth tag", () => {
    const envelope = sealEnvelope(key, plaintext);
    envelope[envelope.length - 1] ^= 0x01;
    expect(() => openEnvelope(key, envelope)).toThrow(
      /failed authentication/,
    );
  });

  it("rejects the wrong key", () => {
    const envelope = sealEnvelope(key, plaintext);
    expect(() => openEnvelope(randomBytes(32), envelope)).toThrow(
      EnvelopeError,
    );
  });

  it("rejects an unknown version byte", () => {
    const envelope = sealEnvelope(key, plaintext);
    envelope[0] = 0x02;
    expect(() => openEnvelope(key, envelope)).toThrow(/version/);
  });

  it("rejects truncated envelopes", () => {
    expect(() => openEnvelope(key, Buffer.from([ENVELOPE_VERSION, 1, 2]))).toThrow(
      /truncated/,
    );
  });
});

describe("loadMasterKey", () => {
  it("fails loudly when CREDENTIALS_MASTER_KEY is unset, pointing at the root .env and the docs", () => {
    expect(() => loadMasterKey({})).toThrow(CredentialKeyError);
    let message = "";
    try {
      loadMasterKey({});
    } catch (err) {
      message = (err as Error).message;
    }
    expect(message).toMatch(/^CREDENTIALS_MASTER_KEY is not set/);
    expect(message).toMatch(/openssl rand -base64 32/);
    expect(message).toMatch(/root \.env/);
    expect(message).toMatch(/docs\/connections\.md/);
    expect(message).not.toMatch(/\.env\.local/);
  });

  it("rejects keys that are not 32 bytes after base64 decoding", () => {
    expect(() =>
      loadMasterKey({ CREDENTIALS_MASTER_KEY: Buffer.from("short").toString("base64") }),
    ).toThrow(/32 bytes/);
  });

  it("accepts a base64-encoded 32-byte key", () => {
    const k = loadMasterKey({
      CREDENTIALS_MASTER_KEY: randomBytes(32).toString("base64"),
    });
    expect(k.length).toBe(32);
  });
});

describe("LocalKeyEncryptionProvider", () => {
  it("round-trips through the provider interface", () => {
    const provider = new LocalKeyEncryptionProvider(key);
    expect(provider.decrypt(provider.encrypt(plaintext))).toBe(plaintext);
  });
});
