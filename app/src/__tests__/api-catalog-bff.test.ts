// @vitest-environment node
// (server-side BFF route — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/http/safe-fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/http/safe-fetch")>();
  return { ...actual, safeFetch: vi.fn() };
});
vi.mock("@/lib/auth/config", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/config")>();
  return { ...actual, getAuthConfig: vi.fn() };
});
vi.mock("@/lib/auth/session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/session")>();
  return { ...actual, readSession: vi.fn() };
});

import { safeFetch } from "@/lib/http/safe-fetch";
import { getAuthConfig } from "@/lib/auth/config";
import { readSession } from "@/lib/auth/session";
import {
  POST as postRoute,
  PUT as putRoute,
  DELETE as deleteRoute,
  builtinCatalogUrl,
} from "@/pages/api/catalog/[...path]";

function authCfg(mode: "bypass" | "oidc") {
  return {
    mode,
    issuer: "http://localhost:8180/realms/stac-higher",
    internalIssuer: "http://localhost:8180/realms/stac-higher",
    clientId: "stac-higher-app",
    redirectUri: "http://localhost:4321/api/auth/callback",
    sessionSecret: mode === "oidc" ? "test-secret" : null,
    sessionMaxAgeS: 28800,
    prod: false,
  };
}

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  path: string,
  { method = "POST", body }: { method?: string; body?: unknown } = {},
) {
  const url = new URL(`http://localhost:4321/api/catalog/${path}`);
  const request = new Request(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  return handler({
    url,
    request,
    params: { path },
    cookies: { get: () => undefined, set: () => {}, delete: () => {} },
    locals: {},
  } as never);
}

function upstream(status = 201, jsonBody: unknown = { id: "c1" }) {
  return new Response(JSON.stringify(jsonBody), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAuthConfig).mockReturnValue(authCfg("bypass") as never);
  vi.mocked(safeFetch).mockResolvedValue(upstream() as never);
});

describe("path scoping", () => {
  it.each([
    ["POST", postRoute, "search"],
    ["POST", postRoute, "collections/c1"], // POST on a single collection
    ["PUT", putRoute, "collections"], // PUT on the list
    ["PUT", putRoute, "collections/c1/items"], // PUT on the items list
    ["POST", postRoute, "conformance"],
  ] as const)("404s non-transaction shapes (%s /%s)", async (method, handler, path) => {
    const res = await call(handler, path, { method, body: {} });
    expect(res.status).toBe(404);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it.each([
    ["POST", postRoute, "collections"],
    ["POST", postRoute, "collections/c1/items"],
    ["PUT", putRoute, "collections/c1"],
    ["PUT", putRoute, "collections/c1/items/i1"],
    ["DELETE", deleteRoute, "collections/c1"],
  ] as const)("forwards transaction shapes (%s /%s)", async (method, handler, path) => {
    const res = await call(handler, path, {
      method,
      body: method === "DELETE" ? undefined : { id: "x" },
    });
    expect(res.status).toBe(201);
    expect(safeFetch).toHaveBeenCalledWith(
      `${builtinCatalogUrl()}/${path}`,
      expect.objectContaining({ method }),
    );
  });
});

describe("token injection (ADR 0008)", () => {
  it("bypass mode forwards with no Authorization header", async () => {
    await call(postRoute, "collections", { body: { id: "c1" } });
    const opts = vi.mocked(safeFetch).mock.calls[0][1] as {
      headers: Record<string, string>;
    };
    expect(opts.headers.authorization).toBeUndefined();
    expect(readSession).not.toHaveBeenCalled();
  });

  it("oidc mode injects the session access token", async () => {
    vi.mocked(getAuthConfig).mockReturnValue(authCfg("oidc") as never);
    vi.mocked(readSession).mockResolvedValue({
      accessToken: "the-access-token",
      refreshToken: null,
      idToken: null,
      expiresAt: 9999999999,
    });
    await call(putRoute, "collections/c1", { method: "PUT", body: { id: "c1" } });
    const opts = vi.mocked(safeFetch).mock.calls[0][1] as {
      headers: Record<string, string>;
    };
    expect(opts.headers.authorization).toBe("Bearer the-access-token");
  });

  it("oidc mode without a session is a 401, nothing forwarded", async () => {
    vi.mocked(getAuthConfig).mockReturnValue(authCfg("oidc") as never);
    vi.mocked(readSession).mockResolvedValue(null);
    const res = await call(postRoute, "collections", { body: { id: "c1" } });
    expect(res.status).toBe(401);
    expect(safeFetch).not.toHaveBeenCalled();
  });
});

describe("forwarding behavior", () => {
  it("passes the JSON body through and returns the upstream status/body", async () => {
    vi.mocked(safeFetch).mockResolvedValue(
      upstream(200, { id: "c9", description: "updated" }) as never,
    );
    const res = await call(putRoute, "collections/c9", {
      method: "PUT",
      body: { id: "c9", description: "updated" },
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ id: "c9", description: "updated" });
    const opts = vi.mocked(safeFetch).mock.calls[0][1] as {
      body: ArrayBuffer;
      headers: Record<string, string>;
    };
    expect(opts.headers["content-type"]).toContain("application/json");
    expect(new TextDecoder().decode(opts.body)).toContain('"updated"');
  });

  it("maps upstream failures to a 502", async () => {
    vi.mocked(safeFetch).mockRejectedValue(new Error("connect ECONNREFUSED"));
    const res = await call(postRoute, "collections", { body: { id: "c1" } });
    expect(res.status).toBe(502);
  });
});
