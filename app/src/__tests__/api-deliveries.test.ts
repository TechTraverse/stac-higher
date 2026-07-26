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
vi.mock("@/lib/associations/deliveries", () => ({
  listDeliveries: vi.fn(),
  getDelivery: vi.fn(),
  redeliverDeadRow: vi.fn(),
}));

import { getAssociation } from "@/lib/associations/storage";
import type { AssociationWithGroup } from "@/lib/associations/storage";
import { getCollectionSettings } from "@/lib/collections/settings";
import {
  getDelivery,
  listDeliveries,
  redeliverDeadRow,
} from "@/lib/associations/deliveries";
import type { ApiDelivery } from "@/lib/associations/deliveries";
import { GET as deliveriesRoute } from "@/pages/api/collections/[id]/connections/[assocId]/deliveries";
import { POST as redeliverRoute } from "@/pages/api/collections/[id]/connections/[assocId]/deliveries/[deliveryId]/redeliver";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

const COLLECTION = "sentinel-2";
const ASSOC_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const DELIVERY_ID = "3a9f1c2e-0000-4000-8000-0000000000d1";
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

function delivery(overrides: Partial<ApiDelivery> = {}): ApiDelivery {
  return {
    id: DELIVERY_ID,
    association_id: ASSOC_ID,
    item_id: "item-1",
    status: "dead",
    attempts: 5,
    bytes: null,
    error: "connection refused",
    next_attempt_at: null,
    delivered_at: null,
    created_at: "2026-07-25T00:00:00.000Z",
    updated_at: "2026-07-25T01:00:00.000Z",
    ...overrides,
  };
}

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
  method = "GET",
) {
  const url = new URL(
    `http://localhost:4321/api/collections/${COLLECTION}/connections/${ASSOC_ID}/deliveries`,
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
  vi.mocked(listDeliveries).mockResolvedValue({
    deliveries: [delivery()],
    counts: { pending: 0, delivering: 0, delivered: 2, failed: 0, dead: 1 },
  });
  vi.mocked(redeliverDeadRow).mockResolvedValue(
    delivery({ status: "failed", attempts: 0 }),
  );
  vi.mocked(getDelivery).mockResolvedValue(delivery());
});

describe("GET .../deliveries", () => {
  const params = { id: COLLECTION, assocId: ASSOC_ID };

  it("200s for a member with visibility, returning rows + counts", async () => {
    const res = await call(deliveriesRoute, authed(["member"]), params);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.deliveries).toHaveLength(1);
    expect(body.counts.dead).toBe(1);
    expect(listDeliveries).toHaveBeenCalledWith(ASSOC_ID);
  });

  it("401s an unauthenticated caller", async () => {
    const anon: AuthContext = { authenticated: false, mode: "oidc" };
    const res = await call(deliveriesRoute, anon, params);
    expect(res.status).toBe(401);
  });

  it("400s an ingest association", async () => {
    vi.mocked(getAssociation).mockResolvedValue(
      assoc({ direction: "ingest", config: { source_path: "/out" } }),
    );
    const res = await call(deliveriesRoute, authed(["member"]), params);
    expect(res.status).toBe(400);
    expect(listDeliveries).not.toHaveBeenCalled();
  });

  it("404s an association outside the caller's groups (owned collection)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      ...unowned,
      groupId: "other-group",
    } as never);
    const res = await call(
      deliveriesRoute,
      authed(["member"], ["not-eo"]),
      params,
    );
    expect(res.status).toBe(404);
  });
});

describe("POST .../deliveries/[deliveryId]/redeliver", () => {
  const params = { id: COLLECTION, assocId: ASSOC_ID, deliveryId: DELIVERY_ID };

  it("202s for an operator on a dead delivery, resetting the cycle", async () => {
    const res = await call(redeliverRoute, authed(["operator"]), params, "POST");
    expect(res.status).toBe(202);
    const body = await res.json();
    expect(body.delivery.status).toBe("failed");
    expect(body.delivery.attempts).toBe(0);
    expect(redeliverDeadRow).toHaveBeenCalledWith(ASSOC_ID, DELIVERY_ID);
  });

  it("403s a member (operator+ action)", async () => {
    const res = await call(redeliverRoute, authed(["member"]), params, "POST");
    expect(res.status).toBe(403);
    expect(redeliverDeadRow).not.toHaveBeenCalled();
  });

  it("400s an ingest association", async () => {
    vi.mocked(getAssociation).mockResolvedValue(
      assoc({ direction: "ingest", config: { source_path: "/out" } }),
    );
    const res = await call(redeliverRoute, authed(["operator"]), params, "POST");
    expect(res.status).toBe(400);
  });

  it("409s a disabled association", async () => {
    vi.mocked(getAssociation).mockResolvedValue(assoc({ enabled: false }));
    const res = await call(redeliverRoute, authed(["operator"]), params, "POST");
    expect(res.status).toBe(409);
    expect(redeliverDeadRow).not.toHaveBeenCalled();
  });

  it("409s a delivery that exists but is not dead", async () => {
    vi.mocked(redeliverDeadRow).mockResolvedValue(null);
    vi.mocked(getDelivery).mockResolvedValue(delivery({ status: "delivered" }));
    const res = await call(redeliverRoute, authed(["operator"]), params, "POST");
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toContain("delivered");
  });

  it("404s an unknown or non-uuid delivery id", async () => {
    vi.mocked(redeliverDeadRow).mockResolvedValue(null);
    vi.mocked(getDelivery).mockResolvedValue(null);
    const res = await call(redeliverRoute, authed(["operator"]), params, "POST");
    expect(res.status).toBe(404);

    const bad = await call(
      redeliverRoute,
      authed(["operator"]),
      { ...params, deliveryId: "not-a-uuid" },
      "POST",
    );
    expect(bad.status).toBe(404);
    expect(redeliverDeadRow).toHaveBeenCalledTimes(1);
  });
});
