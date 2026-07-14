import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ALL } from "@/pages/api/proxy";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
  vi.stubEnv("SAFE_FETCH_ALLOW_HOSTS", "example.com");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function makeRequest(
  headers: Record<string, string> = {},
  options: { method?: string; body?: string } = {},
): Request {
  return new Request("http://localhost:4321/api/proxy", {
    method: options.method ?? "GET",
    headers,
    body: options.body,
  });
}

function makeContext(request: Request) {
  return { request } as Parameters<typeof ALL>[0];
}

describe("proxy API route", () => {
  it("returns 400 when X-Proxy-Target header is missing", async () => {
    const req = makeRequest({ "X-Proxy-Endpoint": "http://example.com" });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toMatch(/Missing X-Proxy-Target/);
  });

  it("returns 400 when X-Proxy-Endpoint header is missing", async () => {
    const req = makeRequest({ "X-Proxy-Target": "http://example.com/collections" });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toMatch(/Missing X-Proxy-Endpoint/);
  });

  it("returns 400 for invalid target URL", async () => {
    const req = makeRequest({
      "X-Proxy-Target": "not-a-url",
      "X-Proxy-Endpoint": "http://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toMatch(/Invalid target URL/);
  });

  it("returns 403 for non-http scheme", async () => {
    const req = makeRequest({
      "X-Proxy-Target": "ftp://example.com/collections",
      "X-Proxy-Endpoint": "ftp://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.error).toMatch(/http or https/);
  });

  it("returns 403 when target does not match endpoint base", async () => {
    const req = makeRequest({
      "X-Proxy-Target": "http://evil.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.error).toMatch(/does not match/);
  });

  it("forwards GET request to target URL", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ collections: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
      Accept: "application/json",
    });
    const res = await ALL(makeContext(req));

    expect(mockFetch).toHaveBeenCalledOnce();
    const [getUrl, getOpts] = mockFetch.mock.calls[0];
    expect(getUrl).toBe("http://example.com/collections");
    expect(getOpts.method).toBe("GET");
    expect(getOpts.headers).toEqual({ accept: "application/json" });
    expect(getOpts.body).toBeUndefined();
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual({ collections: [] });
  });

  it("forwards POST request with body", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ id: "test" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const postBody = JSON.stringify({ id: "test-collection" });
    const req = makeRequest(
      {
        "X-Proxy-Target": "http://example.com/collections",
        "X-Proxy-Endpoint": "http://example.com",
        "Content-Type": "application/json",
      },
      { method: "POST", body: postBody },
    );
    const res = await ALL(makeContext(req));

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("http://example.com/collections");
    expect(opts.method).toBe("POST");
    expect(opts.headers["content-type"]).toBe("application/json");
    expect(res.status).toBe(201);
  });

  it("forwards upstream response status code", async () => {
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections/missing",
      "X-Proxy-Endpoint": "http://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(404);
  });

  it("forwards response Content-Type header", async () => {
    mockFetch.mockResolvedValue(
      new Response('{"data": true}', {
        status: 200,
        headers: { "Content-Type": "application/geo+json" },
      }),
    );

    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/search",
      "X-Proxy-Endpoint": "http://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.headers.get("Content-Type")).toBe("application/geo+json");
  });

  it("returns 403 when target resolves to loopback (SSRF guard)", async () => {
    const req = makeRequest({
      "X-Proxy-Target": "http://127.0.0.1:9999/collections",
      "X-Proxy-Endpoint": "http://127.0.0.1:9999",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.error).toMatch(/private|loopback/i);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns 413 when request body exceeds size cap", async () => {
    const big = "x".repeat(16);
    const req = makeRequest(
      {
        "X-Proxy-Target": "http://example.com/collections",
        "X-Proxy-Endpoint": "http://example.com",
        "Content-Type": "application/json",
        "Content-Length": String(10 * 1024 * 1024),
      },
      { method: "POST", body: big },
    );
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(413);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns 403 when Sec-Fetch-Site is cross-site", async () => {
    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
      "Sec-Fetch-Site": "cross-site",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(403);
    const body = await res.json();
    expect(body.error).toMatch(/Cross-site/);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("allows same-origin Sec-Fetch-Site", async () => {
    mockFetch.mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
      "Sec-Fetch-Site": "same-origin",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(200);
  });

  it("returns 401 when PROXY_AUTH_TOKEN is set and X-Proxy-Auth is missing", async () => {
    vi.stubEnv("PROXY_AUTH_TOKEN", "supersecret");
    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(401);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns 401 when X-Proxy-Auth does not match PROXY_AUTH_TOKEN", async () => {
    vi.stubEnv("PROXY_AUTH_TOKEN", "supersecret");
    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
      "X-Proxy-Auth": "wrong",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(401);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("forwards request when X-Proxy-Auth matches PROXY_AUTH_TOKEN", async () => {
    vi.stubEnv("PROXY_AUTH_TOKEN", "supersecret");
    mockFetch.mockResolvedValue(
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
      "X-Proxy-Auth": "supersecret",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(200);
    expect(mockFetch).toHaveBeenCalledOnce();
  });

  it("returns 502 when upstream fetch fails", async () => {
    mockFetch.mockRejectedValue(new Error("Connection refused"));

    const req = makeRequest({
      "X-Proxy-Target": "http://example.com/collections",
      "X-Proxy-Endpoint": "http://example.com",
    });
    const res = await ALL(makeContext(req));
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toMatch(/Connection refused/);
  });
});
