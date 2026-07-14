// @vitest-environment node
// (server-side code — jose's WebCrypto checks fail across jsdom realms)
import { describe, it, expect } from "vitest";
import {
  COOKIE_CHUNK_SIZE,
  SESSION_COOKIE,
  chunkValue,
  clearSession,
  openValue,
  readChunkedCookie,
  readSession,
  sealValue,
  writeChunkedCookie,
  writeSession,
  type CookieJar,
  type SessionData,
} from "@/lib/auth/session";

const SECRET = "test-session-secret-with-plenty-of-entropy";

/** Minimal in-memory CookieJar. */
function makeJar(): CookieJar & { store: Map<string, string> } {
  const store = new Map<string, string>();
  return {
    store,
    get(name) {
      const value = store.get(name);
      return value === undefined ? undefined : { value };
    },
    set(name, value) {
      store.set(name, value);
    },
    delete(name) {
      store.delete(name);
    },
  };
}

describe("sealValue / openValue", () => {
  it("round-trips a payload", async () => {
    const sealed = await sealValue({ hello: "world", n: 42 }, SECRET, 60);
    const opened = await openValue<{ hello: string; n: number }>(sealed, SECRET);
    expect(opened?.hello).toBe("world");
    expect(opened?.n).toBe(42);
  });

  it("produces an opaque (encrypted) token", async () => {
    const sealed = await sealValue({ secretValue: "do-not-leak" }, SECRET, 60);
    expect(sealed).not.toContain("do-not-leak");
    // JWE compact serialization has 5 parts (vs 3 for a plain JWS).
    expect(sealed.split(".")).toHaveLength(5);
  });

  it("returns null with the wrong secret", async () => {
    const sealed = await sealValue({ a: 1 }, SECRET, 60);
    expect(await openValue(sealed, "some-other-secret")).toBeNull();
  });

  it("returns null when expired", async () => {
    const sealed = await sealValue({ a: 1 }, SECRET, -10);
    expect(await openValue(sealed, SECRET)).toBeNull();
  });

  it("returns null on garbage input", async () => {
    expect(await openValue("not-a-token", SECRET)).toBeNull();
  });
});

describe("cookie chunking", () => {
  it("keeps small values in a single cookie", () => {
    const jar = makeJar();
    writeChunkedCookie(jar, "c", "small", { path: "/" });
    expect(jar.store.get("c")).toBe("small");
    expect(jar.store.has("c.1")).toBe(false);
    expect(readChunkedCookie(jar, "c")).toBe("small");
  });

  it("splits and reassembles values larger than the chunk size", () => {
    const jar = makeJar();
    const big = "x".repeat(COOKIE_CHUNK_SIZE * 2 + 100);
    writeChunkedCookie(jar, "c", big, { path: "/" });
    expect(jar.store.has("c")).toBe(true);
    expect(jar.store.has("c.1")).toBe(true);
    expect(jar.store.has("c.2")).toBe(true);
    expect(jar.store.has("c.3")).toBe(false);
    expect(readChunkedCookie(jar, "c")).toBe(big);
  });

  it("removes stale chunks when a session shrinks", () => {
    const jar = makeJar();
    writeChunkedCookie(jar, "c", "x".repeat(COOKIE_CHUNK_SIZE + 1), { path: "/" });
    expect(jar.store.has("c.1")).toBe(true);
    writeChunkedCookie(jar, "c", "tiny", { path: "/" });
    expect(jar.store.has("c.1")).toBe(false);
    expect(readChunkedCookie(jar, "c")).toBe("tiny");
  });

  it("chunkValue never returns an empty list", () => {
    expect(chunkValue("")).toEqual([""]);
  });
});

describe("session round-trip", () => {
  const cfg = {
    sessionSecret: SECRET,
    sessionMaxAgeS: 3600,
    redirectUri: "http://localhost:4321/api/auth/callback",
  };

  const session: SessionData = {
    // Realistic sizes: three Keycloak-ish JWTs force chunking.
    accessToken: "a".repeat(1800),
    refreshToken: "r".repeat(1200),
    idToken: "i".repeat(1500),
    expiresAt: Math.floor(Date.now() / 1000) + 300,
  };

  it("writes, reads back, and clears a session", async () => {
    const jar = makeJar();
    await writeSession(jar, session, cfg);
    expect(jar.store.has(SESSION_COOKIE)).toBe(true);

    const read = await readSession(jar, SECRET);
    expect(read).toEqual(session);

    clearSession(jar);
    expect(await readSession(jar, SECRET)).toBeNull();
    expect(jar.store.size).toBe(0);
  });

  it("returns null for a tampered cookie", async () => {
    const jar = makeJar();
    await writeSession(jar, session, cfg);
    jar.store.set(SESSION_COOKIE, "A" + jar.store.get(SESSION_COOKIE)!.slice(1));
    expect(await readSession(jar, SECRET)).toBeNull();
  });

  it("returns null when the session outlived its max age", async () => {
    const jar = makeJar();
    await writeSession(jar, session, { ...cfg, sessionMaxAgeS: -10 });
    expect(await readSession(jar, SECRET)).toBeNull();
  });

  it("returns null with a different secret", async () => {
    const jar = makeJar();
    await writeSession(jar, session, cfg);
    expect(await readSession(jar, "rotated-secret")).toBeNull();
  });
});
