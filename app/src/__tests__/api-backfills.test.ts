// @vitest-environment node
// (server-side routes — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/associations/storage", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/associations/storage")>();
  return { ...actual, getAssociation: vi.fn() };
});
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
}));
vi.mock("@/lib/associations/backfills", () => ({
  insertBackfill: vi.fn(),
  getBackfill: vi.fn(),
}));

import { getAssociation } from "@/lib/associations/storage";
import type { AssociationWithGroup } from "@/lib/associations/storage";
import { getCollectionSettings } from "@/lib/collections/settings";
import { getBackfill, insertBackfill } from "@/lib/associations/backfills";
import type { ApiBackfill } from "@/lib/associations/backfills";
import { POST as backfillRoute } from "@/pages/api/collections/[id]/connections/[assocId]/backfill";
import { GET as pollRoute } from "@/pages/api/collections/[id]/connections/[assocId]/backfills/[backfillId]";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

const COLLECTION = "sentinel-2";
const ASSOC_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const BACKFILL_ID = "3a9f1c2e-0000-4000-8000-0000000000b1";
const EO = "earth-observation";

function assoc(
  overrides: Partial<AssociationWithGroup> = {},
): AssociationWithGroup {
  return {
    id: ASSOC_ID,
    collection_id: COLLECTION,
    connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
    direction: "deliver",
    enabled: true,
    config: { path_template: "{collection}/{item_id}/{filename}" },
    expectation: null,
    flow_stats: {},
    created_by: "user-1",
    created_at: "2026-06-01T00:00:00.000Z",
    updated_at: "2026-06-02T00:00:00.000Z",
    connection: { name: "S3 dest", protocol: "s3", status: "ok" },
    connectionGroupId: EO,
    ...overrides,
  };
}

const backfill: ApiBackfill = {
  id: BACKFILL_ID,
  association_id: ASSOC_ID,
  requested_by: "user-1",
  status: "queued",
  items_enqueued: 0,
  error: null,
  created_at: "2026-07-25T00:00:00.000Z",
  started_at: null,
  finished_at: null,
};

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  params: Record<string, string>,
  method = "POST",
) {
  const url = new URL(
    `http://localhost:4321/api/collections/${COLLECTION}/connections/${ASSOC_ID}/backfill`,
  );
  return handler({
    url,
    locals: { auth },
    request: new Request(url, { method }),
    params,
  } as never);
}

const unowned = {
  collectionId: COLLECTION,
  groupId: null,
  externallyWritable: false,
  retentionDays: null,
  gcGraceDays: 30,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAssociation).mockResolvedValue(assoc());
  vi.mocked(getCollectionSettings).mockResolvedValue(unowned as never);
  vi.mocked(insertBackfill).mockResolvedValue(backfill);
  vi.mocked(getBackfill).mockResolvedValue(backfill);
});

describe("POST .../backfill", () => {
  const params = { id: COLLECTION, assocId: ASSOC_ID };

  it("202s for an operator on an enabled deliver association", async () => {
    const res = await call(backfillRoute, authed(["operator"]), params);
    expect(res.status).toBe(202);
    const body = await res.json();
    expect(body.backfill.id).toBe(BACKFILL_ID);
    expect(insertBackfill).toHaveBeenCalledWith(ASSOC_ID, "user-1");
  });

  it("403s a member (operator+ action)", async () => {
    const res = await call(backfillRoute, authed(["member"]), params);
    expect(res.status).toBe(403);
    expect(insertBackfill).not.toHaveBeenCalled();
  });

  it("400s an ingest association", async () => {
    vi.mocked(getAssociation).mockResolvedValue(
      assoc({
        direction: "ingest",
        config: {
          source_path: "/out",
          poll_frequency_seconds: 300,
          storage_mode: "copy",
        },
      }),
    );
    const res = await call(backfillRoute, authed(["operator"]), params);
    expect(res.status).toBe(400);
    expect(insertBackfill).not.toHaveBeenCalled();
  });

  it("409s a disabled association", async () => {
    vi.mocked(getAssociation).mockResolvedValue(assoc({ enabled: false }));
    const res = await call(backfillRoute, authed(["operator"]), params);
    expect(res.status).toBe(409);
    expect(insertBackfill).not.toHaveBeenCalled();
  });

  it("409s when a backfill is already queued or running (atomic insert conflict)", async () => {
    vi.mocked(insertBackfill).mockResolvedValue(null);
    const res = await call(backfillRoute, authed(["operator"]), params);
    expect(res.status).toBe(409);
  });

  it("404s an association outside the caller's groups (owned collection)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      ...unowned,
      groupId: "other-group",
    } as never);
    const res = await call(
      backfillRoute,
      authed(["operator"], ["not-eo"]),
      params,
    );
    expect(res.status).toBe(404);
  });
});

describe("GET .../backfills/[backfillId]", () => {
  const params = { id: COLLECTION, assocId: ASSOC_ID, backfillId: BACKFILL_ID };

  it("200s for a member with visibility", async () => {
    const res = await call(pollRoute, authed(["member"]), params, "GET");
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.backfill.status).toBe("queued");
    expect(getBackfill).toHaveBeenCalledWith(ASSOC_ID, BACKFILL_ID);
  });

  it("404s an unknown or non-uuid backfill id", async () => {
    vi.mocked(getBackfill).mockResolvedValue(null);
    const res = await call(pollRoute, authed(["member"]), params, "GET");
    expect(res.status).toBe(404);

    const bad = await call(
      pollRoute,
      authed(["member"]),
      { ...params, backfillId: "not-a-uuid" },
      "GET",
    );
    expect(bad.status).toBe(404);
    expect(getBackfill).toHaveBeenCalledTimes(1);
  });
});
