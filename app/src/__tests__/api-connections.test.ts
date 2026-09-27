// @vitest-environment node
// (server-side routes — no DOM involved)
import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from "vitest";
import { randomBytes } from "node:crypto";

vi.mock("@/lib/connections/deletion", () => ({
  connectionDeleteImpact: vi.fn(),
  softDeleteConnection: vi.fn(),
}));

vi.mock("@/lib/connections/storage", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/connections/storage")>();
  return {
    ...actual,
    listConnections: vi.fn(),
    getConnection: vi.fn(),
    createConnection: vi.fn(),
    updateConnection: vi.fn(),
    resetHostKey: vi.fn(),
    insertConnectionCheck: vi.fn(),
    getConnectionCheck: vi.fn(),
  };
});

import {
  listConnections,
  getConnection,
  createConnection,
  updateConnection,
  resetHostKey,
  insertConnectionCheck,
  getConnectionCheck,
} from "@/lib/connections/storage";
import {
  connectionDeleteImpact,
  softDeleteConnection,
} from "@/lib/connections/deletion";
import type { ApiConnection } from "@/lib/connections/storage";
import { GET as listRoute, POST as createRoute } from "@/pages/api/connections/index";
import {
  GET as getRoute,
  PUT as putRoute,
  DELETE as deleteRoute,
} from "@/pages/api/connections/[id]";
import { GET as impactRoute } from "@/pages/api/connections/[id]/impact";
import { POST as testRoute } from "@/pages/api/connections/[id]/test";
import { GET as pollRoute } from "@/pages/api/connections/[id]/checks/[checkId]";
import { POST as resetRoute } from "@/pages/api/connections/[id]/host-key/reset";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

const CONN_ID = "3a9f1c2e-0000-4000-8000-000000000001";
const CHECK_ID = "3a9f1c2e-0000-4000-8000-00000000000c";

const connection: ApiConnection = {
  id: CONN_ID,
  name: "SFTP drop",
  description: "",
  protocol: "sftp",
  config: { host: "sftp.example.com", port: 22, root_path: "/" },
  credentials_set: true,
  host_key: { fingerprint: "SHA256:abc", pinned_at: "2026-07-01T00:00:00.000Z" },
  group_id: "earth-observation",
  created_by: "user-1",
  created_at: "2026-06-01T00:00:00.000Z",
  updated_at: "2026-06-02T00:00:00.000Z",
  enabled: true,
  status: "ok",
  last_checked_at: null,
  last_error: null,
};

const pendingCheck = {
  id: CHECK_ID,
  connection_id: CONN_ID,
  requested_by: "user-1",
  requested_at: "2026-07-14T00:00:00.000Z",
  status: "pending" as const,
  result: null,
  finished_at: null,
};

function authed(
  roles: CanonicalRole[],
  groups = ["earth-observation"],
): AuthContext {
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

type RouteHandler = (
  ctx: never,
) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  opts: { params?: Record<string, string>; body?: unknown } = {},
) {
  const url = new URL("http://localhost:4321/api/connections");
  const request =
    opts.body === undefined
      ? new Request(url)
      : new Request(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(opts.body),
        });
  return handler({
    url,
    locals: { auth },
    request,
    params: opts.params ?? {},
  } as never);
}

const validCreateBody = {
  name: "New SFTP",
  protocol: "sftp",
  group_id: "earth-observation",
  config: { host: "sftp.example.com" },
  credentials: { username: "ingest", password: "s3cret-password" },
};

beforeAll(() => {
  process.env.CREDENTIALS_MASTER_KEY = randomBytes(32).toString("base64");
});

afterAll(() => {
  delete process.env.CREDENTIALS_MASTER_KEY;
});

beforeEach(() => {
  vi.mocked(listConnections).mockReset().mockResolvedValue([connection]);
  vi.mocked(getConnection).mockReset().mockResolvedValue(connection);
  vi.mocked(createConnection).mockReset().mockResolvedValue(connection);
  vi.mocked(updateConnection).mockReset().mockResolvedValue(connection);
  vi.mocked(connectionDeleteImpact).mockReset().mockResolvedValue(IMPACT);
  vi.mocked(softDeleteConnection).mockReset().mockResolvedValue(true);
  vi.mocked(resetHostKey).mockReset().mockResolvedValue(connection);
  vi.mocked(insertConnectionCheck).mockReset().mockResolvedValue(pendingCheck);
  vi.mocked(getConnectionCheck).mockReset().mockResolvedValue(pendingCheck);
});

describe("GET /api/connections", () => {
  it("rejects anonymous with the guard's 401 shape", async () => {
    const res = await call(listRoute, anonymous);
    expect(res.status).toBe(401);
    expect((await res.json()).code).toBe("unauthenticated");
  });

  it("members see their own groups' rows", async () => {
    const res = await call(listRoute, authed(["member"], ["weather"]));
    expect(res.status).toBe(200);
    expect(listConnections).toHaveBeenCalledWith(["weather"]);
  });

  it("admins see all rows", async () => {
    await call(listRoute, authed(["admin"], ["weather"]));
    expect(listConnections).toHaveBeenCalledWith(null);
  });

  it("responses never contain credential material", async () => {
    const res = await call(listRoute, authed(["member"]));
    const body = await res.json();
    const text = JSON.stringify(body);
    expect(text).not.toContain("password");
    expect(text).not.toContain("private_key");
    expect(body.connections[0].credentials_set).toBe(true);
    expect(body.connections[0]).not.toHaveProperty("credentials");
  });
});

describe("POST /api/connections", () => {
  it("rejects anonymous (401) and members (403)", async () => {
    expect((await call(createRoute, anonymous, { body: validCreateBody })).status).toBe(401);
    const res = await call(createRoute, authed(["member"]), { body: validCreateBody });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(createConnection).not.toHaveBeenCalled();
  });

  it("rejects an operator creating outside their groups", async () => {
    const res = await call(createRoute, authed(["operator"], ["weather"]), {
      body: validCreateBody,
    });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(createConnection).not.toHaveBeenCalled();
  });

  it("lets an operator create in their own group, storing only the envelope", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      body: validCreateBody,
    });
    expect(res.status).toBe(201);
    const input = vi.mocked(createConnection).mock.calls[0][0];
    expect(Buffer.isBuffer(input.encryptedCredentials)).toBe(true);
    expect(input.encryptedCredentials[0]).toBe(0x01); // envelope version byte
    expect(
      input.encryptedCredentials.includes(Buffer.from("s3cret-password")),
    ).toBe(false);
    expect(input).not.toHaveProperty("credentials");
    expect(JSON.stringify(await res.json())).not.toContain("s3cret-password");
  });

  it("lets an admin create in any group", async () => {
    const res = await call(createRoute, authed(["admin"], ["other-group"]), {
      body: validCreateBody,
    });
    expect(res.status).toBe(201);
  });

  it("rejects the reserved stac-api protocol with 400", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      body: { ...validCreateBody, protocol: "stac-api" },
    });
    expect(res.status).toBe(400);
    expect(JSON.stringify(await res.json())).toMatch(/reserved for a future release/);
  });

  it("fails loudly (500, actionable message) when CREDENTIALS_MASTER_KEY is unset", async () => {
    const saved = process.env.CREDENTIALS_MASTER_KEY;
    delete process.env.CREDENTIALS_MASTER_KEY;
    try {
      const res = await call(createRoute, authed(["operator"]), {
        body: validCreateBody,
      });
      expect(res.status).toBe(500);
      expect((await res.json()).error).toMatch(/CREDENTIALS_MASTER_KEY/);
      expect(createConnection).not.toHaveBeenCalled();
    } finally {
      process.env.CREDENTIALS_MASTER_KEY = saved;
    }
  });
});

describe("GET /api/connections/[id]", () => {
  it("returns the connection to a member of the owning group", async () => {
    const res = await call(getRoute, authed(["member"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(200);
    expect((await res.json()).id).toBe(CONN_ID);
  });

  it("hides connections outside the caller's groups as 404", async () => {
    const res = await call(getRoute, authed(["operator"], ["weather"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(404);
  });

  it("admins can read any group's connection", async () => {
    const res = await call(getRoute, authed(["admin"], ["weather"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(200);
  });

  it("404s cleanly on a non-uuid id", async () => {
    const res = await call(getRoute, authed(["admin"]), {
      params: { id: "not-a-uuid" },
    });
    expect(res.status).toBe(404);
    expect(getConnection).not.toHaveBeenCalled();
  });
});

describe("PUT /api/connections/[id]", () => {
  it("rejects members with 403 even in their own group", async () => {
    const res = await call(putRoute, authed(["member"]), {
      params: { id: CONN_ID },
      body: { name: "x" },
    });
    expect(res.status).toBe(403);
    expect(updateConnection).not.toHaveBeenCalled();
  });

  it("404s for operators outside the owning group", async () => {
    const res = await call(putRoute, authed(["operator"], ["weather"]), {
      params: { id: CONN_ID },
      body: { name: "x" },
    });
    expect(res.status).toBe(404);
  });

  it("replaces credentials wholesale when provided, otherwise leaves them untouched", async () => {
    await call(putRoute, authed(["operator"]), {
      params: { id: CONN_ID },
      body: { name: "renamed" },
    });
    expect(
      vi.mocked(updateConnection).mock.calls[0][1].encryptedCredentials,
    ).toBeUndefined();

    await call(putRoute, authed(["operator"]), {
      params: { id: CONN_ID },
      body: { credentials: { username: "u2", password: "new-pass" } },
    });
    const patch = vi.mocked(updateConnection).mock.calls[1][1];
    expect(Buffer.isBuffer(patch.encryptedCredentials)).toBe(true);
    expect(
      patch.encryptedCredentials!.includes(Buffer.from("new-pass")),
    ).toBe(false);
  });

  it("clears the host-key pin when an ssh-family host changes", async () => {
    await call(putRoute, authed(["operator"]), {
      params: { id: CONN_ID },
      body: { config: { host: "other-host.example.com" } },
    });
    expect(vi.mocked(updateConnection).mock.calls[0][1].clearHostKey).toBe(true);
  });

  it("rejects protocol changes with 400", async () => {
    const res = await call(putRoute, authed(["operator"]), {
      params: { id: CONN_ID },
      body: { protocol: "s3" },
    });
    expect(res.status).toBe(400);
  });

  it("blocks moving the connection into a group the caller is not in", async () => {
    const res = await call(putRoute, authed(["operator"]), {
      params: { id: CONN_ID },
      body: { group_id: "someone-elses-group" },
    });
    expect(res.status).toBe(403);
    expect(updateConnection).not.toHaveBeenCalled();
  });
});

const IMPACT = {
  associations: { ingest: 1, deliver: 0 },
  reference_items: [{ collection_id: "goes-west", items: 3 }],
  history: { ingest_files: 12, delivery_log: 4, connection_checks: 2 },
  images: 0,
};

describe("DELETE /api/connections/[id] (ADR 0009 soft delete)", () => {
  it("soft-deletes for an operator and returns the counted impact", async () => {
    vi.mocked(connectionDeleteImpact).mockResolvedValue(IMPACT);
    vi.mocked(softDeleteConnection).mockResolvedValue(true);
    const res = await call(deleteRoute, authed(["operator"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(200);
    expect(softDeleteConnection).toHaveBeenCalledWith(CONN_ID);
    const body = await res.json();
    expect(body.deleted).toBe(true);
    expect(body.impact).toEqual(IMPACT);
  });

  it("404s outside the owning group; 403 for members", async () => {
    expect(
      (
        await call(deleteRoute, authed(["operator"], ["weather"]), {
          params: { id: CONN_ID },
        })
      ).status,
    ).toBe(404);
    expect(
      (await call(deleteRoute, authed(["member"]), { params: { id: CONN_ID } }))
        .status,
    ).toBe(403);
    expect(softDeleteConnection).not.toHaveBeenCalled();
  });
});

describe("GET /api/connections/[id]/impact (ADR 0009 pre-flight)", () => {
  it("returns the counted impact for an operator", async () => {
    vi.mocked(connectionDeleteImpact).mockResolvedValue(IMPACT);
    const res = await call(impactRoute, authed(["operator"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(200);
    expect((await res.json()).impact).toEqual(IMPACT);
  });

  it("404s outside the owning group", async () => {
    const res = await call(impactRoute, authed(["operator"], ["weather"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(404);
  });
});

describe("POST /api/connections/[id]/test (ADR 0004 bridge)", () => {
  it("inserts a pending connection_checks row and returns 202", async () => {
    const res = await call(testRoute, authed(["operator"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(202);
    expect(insertConnectionCheck).toHaveBeenCalledWith(CONN_ID, "user-1");
    expect((await res.json()).check.status).toBe("pending");
  });

  it("requires operator (member → 403) and group ownership (other group → 404)", async () => {
    expect(
      (await call(testRoute, authed(["member"]), { params: { id: CONN_ID } }))
        .status,
    ).toBe(403);
    expect(
      (
        await call(testRoute, authed(["operator"], ["weather"]), {
          params: { id: CONN_ID },
        })
      ).status,
    ).toBe(404);
    expect(insertConnectionCheck).not.toHaveBeenCalled();
  });
});

describe("GET /api/connections/[id]/checks/[checkId]", () => {
  it("returns the check for a member of the owning group", async () => {
    const res = await call(pollRoute, authed(["member"]), {
      params: { id: CONN_ID, checkId: CHECK_ID },
    });
    expect(res.status).toBe(200);
    expect((await res.json()).check.id).toBe(CHECK_ID);
    expect(getConnectionCheck).toHaveBeenCalledWith(CONN_ID, CHECK_ID);
  });

  it("404s on unknown checks and non-uuid check ids", async () => {
    vi.mocked(getConnectionCheck).mockResolvedValue(null);
    expect(
      (
        await call(pollRoute, authed(["member"]), {
          params: { id: CONN_ID, checkId: CHECK_ID },
        })
      ).status,
    ).toBe(404);
    expect(
      (
        await call(pollRoute, authed(["member"]), {
          params: { id: CONN_ID, checkId: "nope" },
        })
      ).status,
    ).toBe(404);
  });
});

describe("POST /api/connections/[id]/host-key/reset", () => {
  it("clears the pin for an operator of the owning group", async () => {
    const res = await call(resetRoute, authed(["operator"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(200);
    expect(resetHostKey).toHaveBeenCalledWith(CONN_ID);
  });

  it("400s for protocols without host keys", async () => {
    vi.mocked(getConnection).mockResolvedValue({
      ...connection,
      protocol: "s3",
      config: { bucket: "b" },
      host_key: null,
    });
    const res = await call(resetRoute, authed(["operator"]), {
      params: { id: CONN_ID },
    });
    expect(res.status).toBe(400);
    expect(resetHostKey).not.toHaveBeenCalled();
  });

  it("requires operator and group ownership", async () => {
    expect(
      (await call(resetRoute, authed(["member"]), { params: { id: CONN_ID } }))
        .status,
    ).toBe(403);
    expect(
      (
        await call(resetRoute, authed(["operator"], ["weather"]), {
          params: { id: CONN_ID },
        })
      ).status,
    ).toBe(404);
  });
});
