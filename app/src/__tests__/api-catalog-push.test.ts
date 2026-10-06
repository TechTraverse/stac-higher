// @vitest-environment node
/**
 * P7-D — the brokered push write path on the BFF route (Phase 7 spec §4.3).
 *
 * Covers: the §4.1 precondition set applied to ALL bearer-identity writes
 * (R1) with session-cookie callers asserted unchanged, bearer-caller token
 * forwarding, staged-href pre-validation (each §4.2 admission rule, with the
 * pinned rejection-reason codes), the `prior_item` snapshot on staged PUTs
 * (first-write-wins + never-a-staged-document, R2), the staged-PATCH
 * rejection (R3), and `X-BFF-Auth` emission when the shared secret is
 * configured. The ADR 0008 baseline behavior keeps its own suite
 * (api-catalog-bff.test.ts).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

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
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
}));
vi.mock("@/lib/gc/marks", () => ({
  markAssetGcTolerant: vi.fn(async () => {}),
}));
// Z-2 hooks (the _cube reservation, sink cleanup on collection delete):
// never let them reach a real DB.
vi.mock("@/lib/cubes/storage", () => ({
  isCubeCollection: vi.fn(async () => false),
  deleteCubeSinksForCollectionTolerant: vi.fn(async () => {}),
}));
// The ledger layer is mocked — an unmocked DB lookup 500s with the Docker
// stack down (the api-assets lesson).
vi.mock("@/lib/uploads/storage", () => ({
  getStagedUpload: vi.fn(),
  bindPriorItemSnapshot: vi.fn(async () => {}),
}));

import { safeFetch } from "@/lib/http/safe-fetch";
import { getAuthConfig } from "@/lib/auth/config";
import { readSession } from "@/lib/auth/session";
import { getCollectionSettings } from "@/lib/collections/settings";
import {
  bindPriorItemSnapshot,
  getStagedUpload,
  type StagedUpload,
} from "@/lib/uploads/storage";
import { makeCollectionSettings } from "./helpers/settings-fixtures";
import { builtinCatalogUrl } from "@/lib/catalog/transactions";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import {
  POST as postRoute,
  PUT as putRoute,
  PATCH as patchRoute,
  DELETE as deleteRoute,
} from "@/pages/api/catalog/[...path]";

const UP1 = "0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060";
const UP2 = "aaaa2f64-8f3a-4a5e-9b7d-1c2e3f405060";
const HREF1 = `staging://${UP1}/B04.tif`;

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

function authed(roles: CanonicalRole[], groups: string[] = ["g1"]): AuthContext {
  return {
    authenticated: true,
    mode: "oidc",
    identity: { sub: "svc-push", email: null, name: null, groups, roles },
  };
}

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  path: string,
  {
    method = "POST",
    body,
    locals = {},
  }: { method?: string; body?: unknown; locals?: Record<string, unknown> } = {},
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
    locals,
  } as never);
}

/** Bearer locals: identity derived from an Authorization: Bearer header. */
function bearerLocals(token = "caller-token", auth: AuthContext = authed(["operator"])) {
  return { auth, bearerToken: token };
}

/** A SafeFetchResult (NOT a Response — the snapshot path decodes `.body`). */
function sfResult(status = 201, jsonBody: unknown = { id: "i1" }) {
  return {
    status,
    headers: new Headers({ "content-type": "application/json" }),
    body: new TextEncoder().encode(JSON.stringify(jsonBody)),
  };
}

function makeStagedUpload(overrides: Partial<StagedUpload> = {}): StagedUpload {
  return {
    id: UP1,
    collectionId: "c1",
    itemId: null,
    createdBy: "svc-push",
    groupId: "g1",
    filenames: ["B04.tif"],
    status: "pending",
    result: null,
    error: null,
    createdAt: "2026-08-30T00:00:00.000Z",
    expiresAt: "2026-08-31T00:00:00.000Z",
    finalizedAt: null,
    ...overrides,
  };
}

function stagedItem(overrides: Record<string, unknown> = {}) {
  return {
    type: "Feature",
    id: "i1",
    collection: "c1",
    geometry: null,
    properties: { datetime: "2026-08-30T00:00:00Z" },
    assets: { b04: { href: HREF1, type: "image/tiff" } },
    ...overrides,
  };
}

function forwardedHeaders(callIndex = 0): Record<string, string> {
  const opts = vi.mocked(safeFetch).mock.calls[callIndex][1] as {
    headers: Record<string, string>;
  };
  return opts.headers;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAuthConfig).mockReturnValue(authCfg("bypass") as never);
  vi.mocked(safeFetch).mockResolvedValue(sfResult() as never);
  vi.mocked(getCollectionSettings).mockResolvedValue(
    makeCollectionSettings({ collectionId: "c1", externallyWritable: true, groupId: "g1" }),
  );
  vi.mocked(getStagedUpload).mockResolvedValue(makeStagedUpload());
});

afterEach(() => {
  vi.unstubAllEnvs();
});

// ---------------------------------------------------------------------------
// R1: the §4.1 precondition set for ALL bearer-identity writes
// ---------------------------------------------------------------------------
describe("bearer-identity preconditions (§4.3 R1)", () => {
  it("refuses a bearer item write when externally_writable is false (missing row = false)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1" }), // fixture default: false
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" }, // metadata-only — no staged hrefs, still gated
      locals: bearerLocals(),
    });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("not_externally_writable");
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("refuses a bearer write from outside the owning group (ADR 0003)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1", externallyWritable: true, groupId: "other" }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
      locals: bearerLocals(),
    });
    expect(res.status).toBe(403);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("admin bearer callers bypass the group rule but not externally_writable", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1", externallyWritable: true, groupId: "other" }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
      locals: bearerLocals("t", authed(["admin"], [])),
    });
    expect(res.status).toBe(201);
  });

  it("refuses a bearer item write into an archived collection (409)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({
        collectionId: "c1",
        externallyWritable: true,
        groupId: "g1",
        archived: true,
      }),
    );
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: { id: "i1" },
      locals: bearerLocals(),
    });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("collection_archived");
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("applies the precondition set to bearer DELETEs and collection writes too", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1" }), // not externally writable
    );
    for (const [handler, path, method] of [
      [deleteRoute, "collections/c1/items/i1", "DELETE"],
      [patchRoute, "collections/c1/items/i1", "PATCH"],
      [putRoute, "collections/c1", "PUT"],
      [deleteRoute, "collections/c1", "DELETE"],
    ] as const) {
      const res = await call(handler, path, {
        method,
        body: method === "DELETE" ? undefined : { id: "x" },
        locals: bearerLocals(),
      });
      expect(res.status).toBe(403);
    }
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("refuses bearer collection creation (no collection to evaluate)", async () => {
    const res = await call(postRoute, "collections", {
      body: { id: "c-new" },
      locals: bearerLocals(),
    });
    expect(res.status).toBe(403);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("fails CLOSED when settings cannot be read for a bearer caller", async () => {
    vi.mocked(getCollectionSettings).mockRejectedValue(new Error("db down"));
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
      locals: bearerLocals(),
    });
    expect(res.status).toBe(503);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("session-cookie callers are untouched: writes anywhere, externally_writable ignored", async () => {
    // Default fixture: externallyWritable false, unowned — the browser UI's
    // posture (ADR 0008) must be preserved exactly (R1's second half).
    vi.mocked(getCollectionSettings).mockResolvedValue(
      makeCollectionSettings({ collectionId: "c1" }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
      locals: {}, // no bearerToken — session/bypass caller
    });
    expect(res.status).toBe(201);
    // ...and settings failures stay best-effort for them (archived check).
    vi.mocked(getCollectionSettings).mockRejectedValue(new Error("db down"));
    vi.mocked(safeFetch).mockResolvedValue(sfResult() as never);
    const res2 = await call(postRoute, "collections/c1/items", {
      body: { id: "i2" },
      locals: {},
    });
    expect(res2.status).toBe(201);
  });
});

// ---------------------------------------------------------------------------
// Token forwarding + X-BFF-Auth
// ---------------------------------------------------------------------------
describe("bearer forwarding and X-BFF-Auth (§4.3 / ADR 0015)", () => {
  it("forwards the caller's OWN bearer token, never a session token", async () => {
    vi.mocked(getAuthConfig).mockReturnValue(authCfg("oidc") as never);
    const res = await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
      locals: bearerLocals("the-callers-token"),
    });
    expect(res.status).toBe(201);
    expect(forwardedHeaders().authorization).toBe("Bearer the-callers-token");
    expect(readSession).not.toHaveBeenCalled();
  });

  it("session callers still get the session access token injected", async () => {
    vi.mocked(getAuthConfig).mockReturnValue(authCfg("oidc") as never);
    vi.mocked(readSession).mockResolvedValue({
      accessToken: "the-session-token",
      refreshToken: null,
      idToken: null,
      expiresAt: 9999999999,
    });
    await call(postRoute, "collections/c1/items", { body: { id: "i1" }, locals: {} });
    expect(forwardedHeaders().authorization).toBe("Bearer the-session-token");
  });

  it("stamps X-BFF-Auth on forwarded writes when the secret is configured", async () => {
    vi.stubEnv("CATALOG_BFF_SHARED_SECRET", "s3cret");
    await call(postRoute, "collections/c1/items", {
      body: { id: "i1" },
      locals: bearerLocals(),
    });
    expect(forwardedHeaders()["X-BFF-Auth"]).toBe("s3cret");
  });

  it("omits X-BFF-Auth when no secret is configured (pass-through unchanged)", async () => {
    await call(postRoute, "collections/c1/items", { body: { id: "i1" }, locals: {} });
    const headers = forwardedHeaders();
    expect(headers["X-BFF-Auth"]).toBeUndefined();
    expect(headers["x-bff-auth"]).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Staged-href pre-validation (Tier 0, §4.2 admission rules)
// ---------------------------------------------------------------------------
describe("staged pre-validation", () => {
  const locals = bearerLocals();

  it("happy path: a valid staged POST is forwarded with the original bytes", async () => {
    const body = stagedItem();
    const res = await call(postRoute, "collections/c1/items", { body, locals });
    expect(res.status).toBe(201);
    expect(getStagedUpload).toHaveBeenCalledWith(UP1);
    const opts = vi.mocked(safeFetch).mock.calls[0][1] as { body: ArrayBuffer };
    expect(JSON.parse(new TextDecoder().decode(opts.body))).toEqual(body);
  });

  it("rejects bad staged-href grammar (400)", async () => {
    const body = stagedItem({ assets: { b: { href: "staging://only-an-id" } } });
    const res = await call(postRoute, "collections/c1/items", { body, locals });
    expect(res.status).toBe(400);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("rejects hrefs from more than one session (multi_session)", async () => {
    const body = stagedItem({
      assets: {
        a: { href: HREF1 },
        b: { href: `staging://${UP2}/B08.tif` },
      },
    });
    const res = await call(postRoute, "collections/c1/items", { body, locals });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("multi_session");
    expect(getStagedUpload).not.toHaveBeenCalled();
  });

  it("rejects an unknown session (unknown_session)", async () => {
    vi.mocked(getStagedUpload).mockResolvedValue(null);
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("unknown_session");
  });

  it("rejects a terminal session (session_terminal)", async () => {
    vi.mocked(getStagedUpload).mockResolvedValue(
      makeStagedUpload({ status: "rejected" }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("session_terminal");
  });

  it("rejects a session minted for another collection (wrong_collection)", async () => {
    vi.mocked(getStagedUpload).mockResolvedValue(
      makeStagedUpload({ collectionId: "c-other" }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("wrong_collection");
  });

  it("rejects a session bound to a different item (bound_to_other_item)", async () => {
    vi.mocked(getStagedUpload).mockResolvedValue(
      makeStagedUpload({ itemId: "someone-elses-item" }),
    );
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("bound_to_other_item");
  });

  it("allows a session already bound to THIS item (re-PUT of the same push)", async () => {
    vi.mocked(getStagedUpload).mockResolvedValue(makeStagedUpload({ itemId: "i1" }));
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(sfResult(404, {}) as never) // snapshot GET: no prior
      .mockResolvedValueOnce(sfResult(200) as never);
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(200);
  });

  it("requires the top-level collection field (400)", async () => {
    const { collection: _omit, ...rest } = stagedItem();
    const res = await call(postRoute, "collections/c1/items", {
      body: rest,
      locals,
    });
    expect(res.status).toBe(400);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("requires the collection field to match the path (400)", async () => {
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem({ collection: "c-else" }),
      locals,
    });
    expect(res.status).toBe(400);
  });

  it("light structural check: rejects a staged body without an id (400)", async () => {
    const { id: _omit, ...rest } = stagedItem();
    const res = await call(postRoute, "collections/c1/items", {
      body: rest,
      locals,
    });
    expect(res.status).toBe(400);
  });

  it("light structural check is NOT full STAC validation (extra keys pass)", async () => {
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem({ properties: {}, bbox: "not-even-a-bbox" }),
      locals,
    });
    expect(res.status).toBe(201);
  });

  it("rejects staged hrefs in a PATCH — staged updates require PUT (R3)", async () => {
    const res = await call(patchRoute, "collections/c1/items/i1", {
      method: "PATCH",
      body: { assets: { b04: { href: HREF1 } } },
      locals,
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatch(/PUT/);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("metadata-only PATCHes pass through (preconditions only, nothing more)", async () => {
    const res = await call(patchRoute, "collections/c1/items/i1", {
      method: "PATCH",
      body: { properties: { "eo:cloud_cover": 12 } },
      locals,
    });
    expect(res.status).toBe(201);
    expect(getStagedUpload).not.toHaveBeenCalled();
  });

  it("rejects staged hrefs in a collection write (400)", async () => {
    const res = await call(putRoute, "collections/c1", {
      method: "PUT",
      body: { id: "c1", summaries: { weird: HREF1 } },
      locals,
    });
    expect(res.status).toBe(400);
    expect(safeFetch).not.toHaveBeenCalled();
  });

  it("also pre-validates staged bodies from session-cookie callers", async () => {
    // The admission rules protect finalize semantics, not authorization —
    // they hold however the identity arrived.
    vi.mocked(getStagedUpload).mockResolvedValue(null);
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem(),
      locals: {},
    });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("unknown_session");
  });
});

// ---------------------------------------------------------------------------
// prior_item snapshot on staged PUTs (R2)
// ---------------------------------------------------------------------------
describe("prior_item snapshot (§4.3 / §6.3 restore point)", () => {
  const locals = bearerLocals();
  const priorDoc = {
    type: "Feature",
    id: "i1",
    collection: "c1",
    geometry: null,
    properties: {},
    assets: { b04: { href: "/api/assets/c1/i1/B04.tif" } },
  };

  it("snapshots the stored item before forwarding a staged PUT", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(sfResult(200, priorDoc) as never) // GET current
      .mockResolvedValueOnce(sfResult(200) as never); // forward
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(200);
    expect(bindPriorItemSnapshot).toHaveBeenCalledWith(UP1, priorDoc);
    // The GET went to the built-in catalog's item URL.
    expect(vi.mocked(safeFetch).mock.calls[0][0]).toBe(
      `${builtinCatalogUrl()}/collections/c1/items/i1`,
    );
    expect(
      (vi.mocked(safeFetch).mock.calls[0][1] as { method: string }).method,
    ).toBe("GET");
  });

  it("never snapshots a stored document that itself carries staged hrefs (R2)", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(
        sfResult(200, { ...priorDoc, assets: { b04: { href: HREF1 } } }) as never,
      )
      .mockResolvedValueOnce(sfResult(200) as never);
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(200);
    expect(bindPriorItemSnapshot).not.toHaveBeenCalled();
  });

  it("skips the snapshot when the item does not exist yet (PUT-as-create)", async () => {
    vi.mocked(safeFetch)
      .mockResolvedValueOnce(sfResult(404, { code: "NotFound" }) as never)
      .mockResolvedValueOnce(sfResult(201) as never);
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(201);
    expect(bindPriorItemSnapshot).not.toHaveBeenCalled();
  });

  it("fails CLOSED when the current item cannot be read (502, nothing forwarded)", async () => {
    vi.mocked(safeFetch).mockResolvedValueOnce(sfResult(500, {}) as never);
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(502);
    expect(vi.mocked(safeFetch)).toHaveBeenCalledTimes(1); // only the GET
    expect(bindPriorItemSnapshot).not.toHaveBeenCalled();
  });

  it("fails CLOSED when the snapshot write fails (500, nothing forwarded)", async () => {
    vi.mocked(safeFetch).mockResolvedValueOnce(sfResult(200, priorDoc) as never);
    vi.mocked(bindPriorItemSnapshot).mockRejectedValueOnce(new Error("db down"));
    const res = await call(putRoute, "collections/c1/items/i1", {
      method: "PUT",
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(500);
    expect(vi.mocked(safeFetch)).toHaveBeenCalledTimes(1);
  });

  it("never snapshots on a staged POST (create — absence is the restore point)", async () => {
    const res = await call(postRoute, "collections/c1/items", {
      body: stagedItem(),
      locals,
    });
    expect(res.status).toBe(201);
    expect(bindPriorItemSnapshot).not.toHaveBeenCalled();
    expect(vi.mocked(safeFetch)).toHaveBeenCalledTimes(1); // forward only, no GET
  });
});
