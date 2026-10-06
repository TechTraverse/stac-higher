// @vitest-environment node
/**
 * /api/collections/[id]/cube-sink (Z-2, virtual cube spec §7): auth, role,
 * group isolation (404), every PUT refusal, the layout lock, PATCH/DELETE.
 * The audit row is the guard's (authz-cube-sink-gate.test.ts pins the gate).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/cubes/storage", () => ({
  getCubeSink: vi.fn(),
  upsertCubeSink: vi.fn(),
  setCubeSinkEnabled: vi.fn(),
  deleteCubeSink: vi.fn(),
  cubeLedgerSummary: vi.fn(),
  existingCollections: vi.fn(),
  referenceIngestSources: vi.fn(),
  collectionHasItem: vi.fn(),
}));
vi.mock("@/lib/associations/access", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/associations/access")>()),
  canManageCollection: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));
vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { canManageCollection } from "@/lib/associations/access";
import {
  collectionHasItem,
  cubeLedgerSummary,
  deleteCubeSink,
  existingCollections,
  getCubeSink,
  referenceIngestSources,
  setCubeSinkEnabled,
  upsertCubeSink,
} from "@/lib/cubes/storage";
import type { ApiCubeSink } from "@/lib/cubes/storage";
import {
  DELETE as deleteRoute,
  GET as getRoute,
  PATCH as patchRoute,
  PUT as putRoute,
} from "@/pages/api/collections/[id]/cube-sink";

const CUBE = "goes19-c13-cube";
const SOURCE = "goes19-cmipc";
const EO = "earth-observation";
const config = { append_dim: "t", variables: ["CMI"], loadable_variables: ["t"] };
const parsedConfig = { ...config, parser: "hdf5", asset_key: "cube", on_late: "skip" };

function sink(overrides: Partial<ApiCubeSink> = {}): ApiCubeSink {
  return {
    id: "3a9f1c2e-0000-4000-8000-0000000000c1", source_collection_id: SOURCE,
    cube_collection_id: CUBE, enabled: true, config: parsedConfig as never, source_prefixes: [],
    last_snapshot_id: null, last_appended_at: null, last_maintained_at: null,
    last_maintenance: null, last_error: null, created_by: "user-1",
    created_at: "2026-10-05T00:00:00.000Z", updated_at: "2026-10-05T00:00:00.000Z",
    ...overrides,
  };
}

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return { authenticated: true, mode: "bypass", identity: { sub: "user-1", email: null, name: null, groups, roles } };
}
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };
const operator = authed(["operator"]);

type RouteHandler = (ctx: never) => Promise<Response> | Response;
function call(handler: RouteHandler, auth: AuthContext, { method = "GET", body }: { method?: string; body?: unknown } = {}) {
  const url = new URL(`http://localhost:4321/api/collections/${CUBE}/cube-sink`);
  return handler({
    url, locals: { auth },
    request: new Request(url, { method, body: body === undefined ? undefined : JSON.stringify(body) }),
    params: { id: CUBE },
  } as never);
}
const put = (body: unknown, auth = operator) => call(putRoute, auth, { method: "PUT", body });

beforeEach(() => {
  vi.mocked(canManageCollection).mockReset().mockResolvedValue(true);
  vi.mocked(getCubeSink).mockReset().mockResolvedValue(null);
  vi.mocked(upsertCubeSink).mockReset().mockResolvedValue({ sink: sink(), created: true });
  vi.mocked(setCubeSinkEnabled).mockReset().mockResolvedValue(sink({ enabled: false }));
  vi.mocked(deleteCubeSink).mockReset().mockResolvedValue(true);
  vi.mocked(cubeLedgerSummary).mockReset().mockResolvedValue({ counts: { pending: 0, appended: 0, skipped: 0, failed: 0 }, recent: [] });
  vi.mocked(existingCollections).mockReset().mockResolvedValue(new Set([CUBE, SOURCE]));
  vi.mocked(referenceIngestSources).mockReset().mockResolvedValue([{ association_id: "a1", connection_id: "c1", anonymous: true }]);
  vi.mocked(collectionHasItem).mockReset().mockResolvedValue(false);
});

describe("GET", () => {
  it("401s anonymous callers", async () => {
    expect((await call(getRoute, anon)).status).toBe(401);
  });
  it("returns the sink and its ledger to a member of the group", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink());
    const res = await call(getRoute, authed(["member"]));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.sink.cube_collection_id).toBe(CUBE);
    expect(body.ledger.counts.appended).toBe(0);
  });
  it("404s outside the caller's groups, even when a sink exists", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink());
    vi.mocked(canManageCollection).mockResolvedValue(false);
    expect((await call(getRoute, operator)).status).toBe(404);
    expect(cubeLedgerSummary).not.toHaveBeenCalled();
  });
  it("404s when there is no sink", async () => {
    expect((await call(getRoute, operator)).status).toBe(404);
  });
});

describe("PUT", () => {
  it("401s anonymous and 403s members (the non-mutating role)", async () => {
    expect((await put({ source_collection_id: SOURCE, config }, anon)).status).toBe(401);
    const res = await put({ source_collection_id: SOURCE, config }, authed(["member"]));
    expect(res.status).toBe(403);
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("404s a cube collection outside the caller's groups", async () => {
    vi.mocked(canManageCollection).mockResolvedValue(false);
    expect((await put({ source_collection_id: SOURCE, config })).status).toBe(404);
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("400s an invalid config and a source equal to the cube", async () => {
    expect((await put({ source_collection_id: SOURCE, config: { ...config, parser: "grib" } })).status).toBe(400);
    expect((await put({ source_collection_id: CUBE, config })).status).toBe(400);
  });
  it.each([
    ["cube_collection_not_found", () => vi.mocked(existingCollections).mockResolvedValue(new Set([SOURCE]))],
    ["source_collection_not_found", () => vi.mocked(existingCollections).mockResolvedValue(new Set([CUBE]))],
    ["no_reference_ingest", () => vi.mocked(referenceIngestSources).mockResolvedValue([])],
    ["signed_source_unsupported", () => vi.mocked(referenceIngestSources).mockResolvedValue([{ association_id: "a1", connection_id: "c1", anonymous: false }])],
  ])("422s %s", async (code, arrange) => {
    arrange();
    const res = await put({ source_collection_id: SOURCE, config });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe(code);
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("treats a source outside the caller's groups as not found, before any lookup", async () => {
    vi.mocked(canManageCollection).mockImplementation(async (_identity, id) => id === CUBE);
    const res = await put({ source_collection_id: SOURCE, config });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("source_collection_not_found");
    expect(existingCollections).not.toHaveBeenCalled();
    expect(referenceIngestSources).not.toHaveBeenCalled();
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("409s when the cube collection already holds an item named _cube", async () => {
    vi.mocked(collectionHasItem).mockResolvedValue(true);
    const res = await put({ source_collection_id: SOURCE, config });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("reserved_item_id");
    expect(collectionHasItem).toHaveBeenCalledWith(CUBE, "_cube");
    expect(upsertCubeSink).not.toHaveBeenCalled();
  });
  it("refuses when any reference association is signed", async () => {
    vi.mocked(referenceIngestSources).mockResolvedValue([
      { association_id: "a1", connection_id: "c1", anonymous: true },
      { association_id: "a2", connection_id: "c2", anonymous: false },
    ]);
    expect((await (await put({ source_collection_id: SOURCE, config })).json()).code).toBe("signed_source_unsupported");
  });
  it("creates (201) with the caller as creator and the parsed config", async () => {
    const res = await put({ source_collection_id: SOURCE, config });
    expect(res.status).toBe(201);
    expect(upsertCubeSink).toHaveBeenCalledWith({
      cubeCollectionId: CUBE, sourceCollectionId: SOURCE, config: parsedConfig, enabled: true, createdBy: "user-1",
    });
  });
  it("replaces (200) an existing sink", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink());
    vi.mocked(upsertCubeSink).mockResolvedValue({ sink: sink(), created: false });
    expect((await put({ source_collection_id: SOURCE, config })).status).toBe(200);
  });
  it("409s a layout change once the repository exists", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink({ last_snapshot_id: "SNAP1" }));
    const res = await put({ source_collection_id: SOURCE, config: { ...config, variables: ["CMI", "DQF"] } });
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("cube_layout_locked");
  });
  it("still accepts a window change once the repository exists", async () => {
    vi.mocked(getCubeSink).mockResolvedValue(sink({ last_snapshot_id: "SNAP1" }));
    vi.mocked(upsertCubeSink).mockResolvedValue({ sink: sink(), created: false });
    const res = await put({ source_collection_id: SOURCE, config: { ...config, window: { max_steps: 72 } } });
    expect(res.status).toBe(200);
  });
});

describe("PATCH", () => {
  it("403s members, 404s outside the group, toggles enabled", async () => {
    expect((await call(patchRoute, authed(["member"]), { method: "PATCH", body: { enabled: false } })).status).toBe(403);
    vi.mocked(canManageCollection).mockResolvedValueOnce(false);
    expect((await call(patchRoute, operator, { method: "PATCH", body: { enabled: false } })).status).toBe(404);
    const res = await call(patchRoute, operator, { method: "PATCH", body: { enabled: false } });
    expect(res.status).toBe(200);
    expect(setCubeSinkEnabled).toHaveBeenCalledWith(CUBE, false);
  });
  it("400s anything but {enabled}, 404s a missing sink", async () => {
    expect((await call(patchRoute, operator, { method: "PATCH", body: { config } })).status).toBe(400);
    vi.mocked(setCubeSinkEnabled).mockResolvedValue(null);
    expect((await call(patchRoute, operator, { method: "PATCH", body: { enabled: true } })).status).toBe(404);
  });
});

describe("DELETE", () => {
  it("403s members, 404s outside the group and a missing sink, deletes otherwise", async () => {
    expect((await call(deleteRoute, authed(["member"]), { method: "DELETE" })).status).toBe(403);
    vi.mocked(canManageCollection).mockResolvedValueOnce(false);
    expect((await call(deleteRoute, operator, { method: "DELETE" })).status).toBe(404);
    vi.mocked(deleteCubeSink).mockResolvedValueOnce(false);
    expect((await call(deleteRoute, operator, { method: "DELETE" })).status).toBe(404);
    const res = await call(deleteRoute, operator, { method: "DELETE" });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ deleted: true });
  });
});
