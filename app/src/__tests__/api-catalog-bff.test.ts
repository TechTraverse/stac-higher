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
// M2-F hooks: keep the archived check + GC marking away from a real DB.
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
}));
vi.mock("@/lib/gc/marks", () => ({
  markAssetGcTolerant: vi.fn(async () => {}),
}));
// Z-2 hooks: the _cube reservation lookup and sink cleanup on collection delete.
vi.mock("@/lib/cubes/storage", () => ({
  isCubeCollection: vi.fn(async () => false),
  deleteCubeSinksForCollectionTolerant: vi.fn(async () => {}),
}));

import { safeFetch } from "@/lib/http/safe-fetch";
import { getAuthConfig } from "@/lib/auth/config";
import { readSession } from "@/lib/auth/session";
import { getCollectionSettings } from "@/lib/collections/settings";
import { markAssetGcTolerant } from "@/lib/gc/marks";
import {
  deleteCubeSinksForCollectionTolerant,
  isCubeCollection,
} from "@/lib/cubes/storage";
import { makeCollectionSettings } from "./helpers/settings-fixtures";
import { builtinCatalogUrl } from "@/lib/catalog/transactions";
import {
  POST as postRoute,
  PUT as putRoute,
  DELETE as deleteRoute,
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
  vi.mocked(getCollectionSettings).mockResolvedValue(
    makeCollectionSettings({ collectionId: "c1" }),
  );
  vi.mocked(isCubeCollection).mockResolvedValue(false);
  vi.mocked(deleteCubeSinksForCollectionTolerant).mockResolvedValue(undefined);
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

describe("retention & GC hooks (M2-F, ADR 0011)", () => {
  it("marks the item prefix after a successful item delete", async () => {
    const res = await call(deleteRoute, "collections/c1/items/i1", {
      method: "DELETE",
    });
    expect(res.ok).toBe(true); // upstream() helper answers 201
    expect(markAssetGcTolerant).toHaveBeenCalledWith({
      collectionId: "c1",
      itemId: "i1",
      reason: "item_delete",
    });
  });

  it("marks the whole-collection prefix after a collection delete", async () => {
    await call(deleteRoute, "collections/c1", { method: "DELETE" });
    expect(markAssetGcTolerant).toHaveBeenCalledWith({
      collectionId: "c1",
      itemId: null,
      reason: "collection_delete",
    });
  });

  it("does not mark when the upstream delete failed", async () => {
    vi.mocked(safeFetch).mockResolvedValue(
      new Response("{}", { status: 404 }) as never,
    );
    await call(deleteRoute, "collections/c1/items/i1", { method: "DELETE" });
    expect(markAssetGcTolerant).not.toHaveBeenCalled();
  });

  it("refuses item writes into an archived collection (409, not forwarded)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1", archived: true }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
    });
    expect(res.status).toBe(409);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("archived collections still allow item deletes and metadata edits", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1", archived: true }),
    );
    expect(
      (await call(deleteRoute, "collections/c1/items/i1", { method: "DELETE" }))
        .ok,
    ).toBe(true);
    // A Response body is single-use — remint the upstream for the second call.
    vi.mocked(safeFetch).mockResolvedValue(upstream() as never);
    expect(
      (
        await call(putRoute, "collections/c1", {
          method: "PUT",
          body: { id: "c1" },
        })
      ).ok,
    ).toBe(true);
  });
});

describe("reserved item id _cube (Z-2, virtual cube spec §7)", () => {
  const item = (id: string) => ({
    type: "Feature", id, collection: "cube", geometry: null, properties: {}, links: [], assets: {},
  });
  const handlers = { POST: postRoute, PUT: putRoute, DELETE: deleteRoute } as const;
  const send = (method: keyof typeof handlers, path: string, body?: unknown) =>
    call(handlers[method], path, { method, body });

  it("refuses POSTing item _cube into a cube collection (422, not forwarded)", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    const res = await send("POST", "collections/cube/items", item("_cube"));
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("reserved_item_id");
    expect(isCubeCollection).toHaveBeenCalledWith("cube");
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("refuses _cube inside a FeatureCollection body", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    const res = await send("POST", "collections/cube/items", {
      type: "FeatureCollection",
      features: [item("a"), item("_cube")],
    });
    expect(res.status).toBe(422);
  });

  it("checks the body id as well as the path id", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    expect((await send("PUT", "collections/cube/items/_cube", item("other"))).status).toBe(422);
    expect((await send("PUT", "collections/cube/items/other", item("_cube"))).status).toBe(422);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("allows _cube in a collection that is not a cube", async () => {
    const res = await send("POST", "collections/plain/items", item("_cube"));
    expect(res.status).toBe(201);
    expect(safeFetch).toHaveBeenCalled();
  });

  it("never queries the sink table for ordinary ids", async () => {
    await send("POST", "collections/cube/items", item("a"));
    expect(isCubeCollection).not.toHaveBeenCalled();
  });

  it("fails closed (503) when the sink lookup errors", async () => {
    vi.mocked(isCubeCollection).mockRejectedValue(new Error("db down"));
    expect((await send("POST", "collections/cube/items", item("_cube"))).status).toBe(503);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("still allows deleting an item named _cube", async () => {
    vi.mocked(isCubeCollection).mockResolvedValue(true);
    vi.mocked(safeFetch).mockResolvedValue(upstream(200) as never);
    expect((await send("DELETE", "collections/cube/items/_cube")).status).toBe(200);
  });
});

describe("collection delete removes cube sinks (Z-2)", () => {
  it("deletes sink rows naming the collection after a successful delete", async () => {
    vi.mocked(safeFetch).mockResolvedValue(upstream(200) as never);
    await call(deleteRoute, "collections/goes19-cmipc", { method: "DELETE" });
    expect(deleteCubeSinksForCollectionTolerant).toHaveBeenCalledWith("goes19-cmipc");
  });

  it("does not touch sinks when the upstream delete failed", async () => {
    vi.mocked(safeFetch).mockResolvedValue(upstream(500) as never);
    await call(deleteRoute, "collections/goes19-cmipc", { method: "DELETE" });
    expect(deleteCubeSinksForCollectionTolerant).not.toHaveBeenCalled();
  });

  it("does not touch sinks on an item delete", async () => {
    vi.mocked(safeFetch).mockResolvedValue(upstream(200) as never);
    await call(deleteRoute, "collections/goes19-cmipc/items/i1", { method: "DELETE" });
    expect(deleteCubeSinksForCollectionTolerant).not.toHaveBeenCalled();
  });
});
