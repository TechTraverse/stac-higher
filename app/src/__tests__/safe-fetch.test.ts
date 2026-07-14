import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  DEFAULT_MAX_BYTES,
  SafeFetchError,
  isPrivateAddress,
  safeFetch,
} from "@/lib/http/safe-fetch";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
  vi.stubEnv("SAFE_FETCH_ALLOW_HOSTS", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("isPrivateAddress", () => {
  it.each([
    ["127.0.0.1", true],
    ["10.0.0.1", true],
    ["172.16.5.5", true],
    ["172.31.255.255", true],
    ["192.168.1.1", true],
    ["169.254.169.254", true],
    ["0.0.0.0", true],
    ["224.0.0.1", true],
    ["8.8.8.8", false],
    ["1.1.1.1", false],
    ["172.32.0.1", false],
    ["::1", true],
    ["::", true],
    ["fc00::1", true],
    ["fd00::1", true],
    ["fe80::1", true],
    ["ff00::1", true],
    ["::ffff:127.0.0.1", true],
    ["::ffff:8.8.8.8", false],
    ["2001:4860:4860::8888", false],
  ])("classifies %s", (addr, expected) => {
    expect(isPrivateAddress(addr)).toBe(expected);
  });
});

describe("safeFetch — URL validation", () => {
  it("rejects invalid URLs", async () => {
    await expect(safeFetch("not-a-url")).rejects.toBeInstanceOf(SafeFetchError);
  });

  it("rejects non-http(s) schemes", async () => {
    await expect(safeFetch("ftp://example.com/x")).rejects.toMatchObject({
      code: "invalid_url",
      status: 403,
    });
  });
});

describe("safeFetch — SSRF guard", () => {
  it("rejects loopback IPv4 literal", async () => {
    await expect(safeFetch("http://127.0.0.1/x")).rejects.toMatchObject({
      code: "blocked_host",
      status: 403,
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("rejects link-local IPv4 literal", async () => {
    await expect(
      safeFetch("http://169.254.169.254/latest/meta-data/"),
    ).rejects.toMatchObject({ code: "blocked_host" });
  });

  it("rejects loopback IPv6 literal", async () => {
    await expect(safeFetch("http://[::1]/x")).rejects.toMatchObject({
      code: "blocked_host",
    });
  });

  it("allows public IPv4 literal", async () => {
    mockFetch.mockResolvedValue(new Response("ok", { status: 200 }));
    const result = await safeFetch("http://8.8.8.8/");
    expect(result.status).toBe(200);
  });

  it("allows hosts on SAFE_FETCH_ALLOW_HOSTS without DNS", async () => {
    vi.stubEnv("SAFE_FETCH_ALLOW_HOSTS", "localhost");
    mockFetch.mockResolvedValue(new Response("ok", { status: 200 }));
    const result = await safeFetch("http://localhost:8082/api");
    expect(result.status).toBe(200);
  });
});

describe("safeFetch — timeout", () => {
  it("throws timeout when upstream aborts with TimeoutError", async () => {
    mockFetch.mockImplementation(async (_url, init) => {
      const signal = (init as RequestInit).signal;
      return new Promise((_res, rej) => {
        signal?.addEventListener("abort", () => {
          rej(Object.assign(new Error("timeout"), { name: "TimeoutError" }));
        });
      });
    });
    await expect(
      safeFetch("http://8.8.8.8/", { timeoutMs: 10 }),
    ).rejects.toMatchObject({ code: "timeout", status: 504 });
  });
});

describe("safeFetch — size cap", () => {
  it("rejects by declared Content-Length", async () => {
    mockFetch.mockResolvedValue(
      new Response("x", {
        status: 200,
        headers: { "Content-Length": String(DEFAULT_MAX_BYTES + 1) },
      }),
    );
    await expect(safeFetch("http://8.8.8.8/")).rejects.toMatchObject({
      code: "too_large",
      status: 413,
    });
  });

  it("aborts mid-stream once maxBytes exceeded", async () => {
    const big = new Uint8Array(100);
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(big);
        controller.enqueue(big);
        controller.close();
      },
    });
    mockFetch.mockResolvedValue(
      new Response(stream, { status: 200 }),
    );
    await expect(
      safeFetch("http://8.8.8.8/", { maxBytes: 150 }),
    ).rejects.toMatchObject({ code: "too_large" });
  });

  it("returns body when under cap", async () => {
    mockFetch.mockResolvedValue(
      new Response("hello", { status: 200 }),
    );
    const result = await safeFetch("http://8.8.8.8/");
    expect(new TextDecoder().decode(result.body)).toBe("hello");
  });
});

describe("safeFetch — observability", () => {
  it("emits a structured info log on success", async () => {
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    mockFetch.mockResolvedValue(new Response("hi", { status: 200 }));

    await safeFetch("http://8.8.8.8/path", { method: "POST" });

    expect(infoSpy).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(infoSpy.mock.calls[0][0] as string);
    expect(payload.event).toBe("safe_fetch");
    expect(payload.method).toBe("POST");
    expect(payload.host).toBe("8.8.8.8");
    expect(payload.status).toBe(200);
    expect(payload.bytes).toBe(2);
    expect(payload.outcome).toBe("ok");
    expect(typeof payload.elapsed_ms).toBe("number");
    infoSpy.mockRestore();
  });

  it("emits a warn log with error code on blocked host", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    await expect(safeFetch("http://127.0.0.1/")).rejects.toBeInstanceOf(
      SafeFetchError,
    );
    expect(warnSpy).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(warnSpy.mock.calls[0][0] as string);
    expect(payload.outcome).toBe("error");
    expect(payload.code).toBe("blocked_host");
    expect(payload.host).toBe("127.0.0.1");
    expect(payload.status).toBe(403);
    warnSpy.mockRestore();
  });

  it("suppresses logging when SAFE_FETCH_LOG=0", async () => {
    vi.stubEnv("SAFE_FETCH_LOG", "0");
    const infoSpy = vi.spyOn(console, "info").mockImplementation(() => {});
    mockFetch.mockResolvedValue(new Response("x", { status: 200 }));
    await safeFetch("http://8.8.8.8/");
    expect(infoSpy).not.toHaveBeenCalled();
    infoSpy.mockRestore();
  });
});
