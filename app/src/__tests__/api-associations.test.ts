// @vitest-environment node
// (server-side routes — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/associations/storage", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/associations/storage")>();
  return {
    ...actual,
    listAssociations: vi.fn(),
    getAssociation: vi.fn(),
    createAssociation: vi.fn(),
    updateAssociation: vi.fn(),
    deleteAssociation: vi.fn(),
  };
});
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
  defaultCollectionSettings: (collectionId: string) => ({
    collectionId,
    groupId: null,
    externallyWritable: false,
    retentionDays: null,
    gcGraceDays: 30,
  }),
}));
vi.mock("@/lib/connections/storage", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/connections/storage")>();
  return { ...actual, getConnection: vi.fn() };
});

import {
  createAssociation,
  deleteAssociation,
  getAssociation,
  listAssociations,
  updateAssociation,
} from "@/lib/associations/storage";
import type { AssociationWithGroup } from "@/lib/associations/storage";
import { getCollectionSettings } from "@/lib/collections/settings";
import { getConnection } from "@/lib/connections/storage";
import type { ApiConnection } from "@/lib/connections/storage";
import { DuplicateAssociationError } from "@/lib/associations/storage";
import {
  GET as listRoute,
  POST as createRoute,
} from "@/pages/api/collections/[id]/connections/index";
import {
  GET as getRoute,
  PUT as putRoute,
  DELETE as deleteRoute,
} from "@/pages/api/collections/[id]/connections/[assocId]";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

const COLLECTION = "sentinel-2";
const CONN_ID = "3a9f1c2e-0000-4000-8000-000000000001";
const ASSOC_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const EO = "earth-observation";

const assoc: AssociationWithGroup = {
  id: ASSOC_ID,
  collection_id: COLLECTION,
  connection_id: CONN_ID,
  direction: "ingest",
  enabled: true,
  config: { source_path: "/out", poll_frequency_seconds: 300, storage_mode: "copy" },
  expectation: null,
  flow_stats: {},
  created_by: "user-1",
  created_at: "2026-06-01T00:00:00.000Z",
  updated_at: "2026-06-02T00:00:00.000Z",
  connection: { name: "S3 source", protocol: "s3", status: "ok" },
  connectionGroupId: EO,
};

const s3Connection = {
  id: CONN_ID,
  protocol: "s3",
  group_id: EO,
} as unknown as ApiConnection;

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anonymous: AuthContext = {
  authenticated: false,
  mode: "oidc",
  identity: null,
};

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  opts: { params?: Record<string, string>; body?: unknown; method?: string } = {},
) {
  const url = new URL(
    `http://localhost:4321/api/collections/${COLLECTION}/connections`,
  );
  const request =
    opts.body === undefined
      ? new Request(url, { method: opts.method ?? "GET" })
      : new Request(url, {
          method: opts.method ?? "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(opts.body),
        });
  return handler({
    url,
    locals: { auth },
    request,
    params: opts.params ?? { id: COLLECTION },
  } as never);
}

const unowned = {
  collectionId: COLLECTION,
  groupId: null,
  externallyWritable: false,
  retentionDays: null,
  gcGraceDays: 30,
};

const validCreateBody = {
  connection_id: CONN_ID,
  direction: "ingest",
  config: { source_path: "/out" },
};

beforeEach(() => {
  vi.mocked(listAssociations).mockReset().mockResolvedValue([assoc]);
  vi.mocked(getAssociation).mockReset().mockResolvedValue(assoc);
  vi.mocked(createAssociation).mockReset().mockResolvedValue(assoc);
  vi.mocked(updateAssociation).mockReset().mockResolvedValue(assoc);
  vi.mocked(deleteAssociation).mockReset().mockResolvedValue(true);
  vi.mocked(getCollectionSettings).mockReset().mockResolvedValue(unowned);
  vi.mocked(getConnection).mockReset().mockResolvedValue(s3Connection);
});

describe("GET /api/collections/[id]/connections", () => {
  it("rejects anonymous with the guard's 401 shape", async () => {
    const res = await call(listRoute, anonymous);
    expect(res.status).toBe(401);
    expect((await res.json()).code).toBe("unauthenticated");
  });

  it("returns associations without leaking the connection group id", async () => {
    const res = await call(listRoute, authed(["operator"]));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.associations).toHaveLength(1);
    expect(body.associations[0]).not.toHaveProperty("connectionGroupId");
    expect(body.associations[0].connection.protocol).toBe("s3");
  });

  it("shows a member an owned collection's rows via their connection group", async () => {
    // Collection owned by another group → not a manager; visible only because
    // the association's connection is in the member's group.
    vi.mocked(getCollectionSettings).mockResolvedValue({
      ...unowned,
      groupId: "weather",
    });
    const res = await call(listRoute, authed(["member"], [EO]));
    expect(res.status).toBe(200);
    expect((await res.json()).associations).toHaveLength(1);
  });

  it("hides rows a caller can neither manage nor own the connection for", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      ...unowned,
      groupId: "weather",
    });
    const res = await call(listRoute, authed(["member"], ["unrelated"]));
    expect(res.status).toBe(200);
    expect((await res.json()).associations).toHaveLength(0);
  });
});

describe("POST /api/collections/[id]/connections", () => {
  it("rejects anonymous (401) and members (403)", async () => {
    expect((await call(createRoute, anonymous, { body: validCreateBody })).status).toBe(401);
    const res = await call(createRoute, authed(["member"]), { body: validCreateBody });
    expect(res.status).toBe(403);
    expect(createAssociation).not.toHaveBeenCalled();
  });

  it("lets an operator create an ingest association on an unowned collection", async () => {
    const res = await call(createRoute, authed(["operator"]), { body: validCreateBody });
    expect(res.status).toBe(201);
    expect(createAssociation).toHaveBeenCalledOnce();
    const body = await res.json();
    expect(body).not.toHaveProperty("connectionGroupId");
  });

  it("rejects managing a collection owned by another group", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      ...unowned,
      groupId: "weather",
    });
    const res = await call(createRoute, authed(["operator"], [EO]), {
      body: validCreateBody,
    });
    expect(res.status).toBe(403);
    expect(createAssociation).not.toHaveBeenCalled();
  });

  it("rejects a connection the caller cannot use (400)", async () => {
    vi.mocked(getConnection).mockResolvedValue(null);
    const res = await call(createRoute, authed(["operator"]), { body: validCreateBody });
    expect(res.status).toBe(400);
    expect(createAssociation).not.toHaveBeenCalled();
  });

  it("allows reference mode for an s3 connection", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      body: { ...validCreateBody, config: { source_path: "/out", storage_mode: "reference" } },
    });
    expect(res.status).toBe(201);
  });

  it("rejects reference mode for a non-s3 connection (400)", async () => {
    vi.mocked(getConnection).mockResolvedValue({
      ...s3Connection,
      protocol: "sftp",
    } as ApiConnection);
    const res = await call(createRoute, authed(["operator"]), {
      body: { ...validCreateBody, config: { source_path: "/out", storage_mode: "reference" } },
    });
    expect(res.status).toBe(400);
    expect(createAssociation).not.toHaveBeenCalled();
  });

  it("maps a duplicate association to 409", async () => {
    vi.mocked(createAssociation).mockRejectedValue(new DuplicateAssociationError());
    const res = await call(createRoute, authed(["operator"]), { body: validCreateBody });
    expect(res.status).toBe(409);
  });

  it("rejects an invalid body (400)", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      body: { connection_id: CONN_ID, direction: "ingest", config: {} },
    });
    expect(res.status).toBe(400);
    expect(createAssociation).not.toHaveBeenCalled();
  });

  it("rejects a delivery association with the reserved message (400)", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      body: { ...validCreateBody, direction: "deliver" },
    });
    expect(res.status).toBe(400);
  });

  it("creates a delivery association (operator)", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      body: {
        connection_id: CONN_ID,
        direction: "deliver",
        config: { path_template: "{collection}/{item_id}/{filename}" },
      },
    });
    expect(res.status).toBe(201);
    expect(createAssociation).toHaveBeenCalledOnce();
  });
});

describe("/api/collections/[id]/connections/[assocId]", () => {
  const params = { id: COLLECTION, assocId: ASSOC_ID };

  it("GET returns a visible association to an operator", async () => {
    const res = await call(getRoute, authed(["operator"]), { params });
    expect(res.status).toBe(200);
    expect((await res.json()).id).toBe(ASSOC_ID);
  });

  it("GET 404s a non-uuid assoc id", async () => {
    const res = await call(getRoute, authed(["operator"]), {
      params: { id: COLLECTION, assocId: "nope" },
    });
    expect(res.status).toBe(404);
  });

  it("GET 404s when the association belongs to a different collection", async () => {
    const res = await call(getRoute, authed(["operator"]), {
      params: { id: "other-collection", assocId: ASSOC_ID },
    });
    expect(res.status).toBe(404);
  });

  it("GET 404s an association the caller cannot see", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      ...unowned,
      groupId: "weather",
    });
    const res = await call(getRoute, authed(["member"], ["unrelated"]), { params });
    expect(res.status).toBe(404);
  });

  it("PUT updates an association for an operator", async () => {
    const res = await call(putRoute, authed(["operator"]), {
      params,
      method: "PUT",
      body: { enabled: false },
    });
    expect(res.status).toBe(200);
    expect(updateAssociation).toHaveBeenCalledOnce();
  });

  it("PUT rejects members (403)", async () => {
    const res = await call(putRoute, authed(["member"]), {
      params,
      method: "PUT",
      body: { enabled: false },
    });
    expect(res.status).toBe(403);
    expect(updateAssociation).not.toHaveBeenCalled();
  });

  it("PUT rejects reference mode against a non-s3 connection (400)", async () => {
    vi.mocked(getConnection).mockResolvedValue({
      ...s3Connection,
      protocol: "sftp",
    } as ApiConnection);
    const res = await call(putRoute, authed(["operator"]), {
      params,
      method: "PUT",
      body: { config: { source_path: "/out", storage_mode: "reference" } },
    });
    expect(res.status).toBe(400);
    expect(updateAssociation).not.toHaveBeenCalled();
  });

  it("DELETE removes an association for an operator (204)", async () => {
    const res = await call(deleteRoute, authed(["operator"]), {
      params,
      method: "DELETE",
    });
    expect(res.status).toBe(204);
    expect(deleteAssociation).toHaveBeenCalledWith(ASSOC_ID);
  });
});
