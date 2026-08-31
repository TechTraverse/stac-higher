// @vitest-environment node
/**
 * /api/processes* routes (M5-A): auth, the group dimension, the deploy verb,
 * and the ADR 0004 test-run bridge. Storage and the collection-side checks
 * are mocked — this pins ROUTE behavior, not SQL.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/processes/storage", () => ({
  listProcesses: vi.fn(),
  getProcess: vi.fn(),
  createProcess: vi.fn(),
  updateProcess: vi.fn(),
  softDeleteProcess: vi.fn(),
  listRevisions: vi.fn(),
  deployRevision: vi.fn(),
  listSources: vi.fn(),
  createSource: vi.fn(),
  updateSource: vi.fn(),
  deleteSource: vi.fn(),
  listOutputs: vi.fn(),
  createOutput: vi.fn(),
  deleteOutput: vi.fn(),
  insertProcessCheck: vi.fn(),
  getProcessCheck: vi.fn(),
  DuplicateProcessNameError: class extends Error {},
  DuplicateSourceError: class extends Error {},
  DuplicateOutputError: class extends Error {},
}));
vi.mock("@/lib/associations/access", () => ({
  canManageCollection: vi.fn(async () => true),
}));
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(async () => ({ archived: false })),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { canManageCollection } from "@/lib/associations/access";
import { getCollectionSettings } from "@/lib/collections/settings";
import {
  createOutput,
  createProcess,
  createSource,
  deployRevision,
  getProcess,
  getProcessCheck,
  insertProcessCheck,
  listProcesses,
  softDeleteProcess,
  updateProcess,
  type ApiProcess,
} from "@/lib/processes/storage";
import {
  GET as listRoute,
  POST as createRoute,
} from "@/pages/api/processes/index";
import {
  DELETE as deleteRoute,
  GET as getRoute,
  PUT as updateRoute,
} from "@/pages/api/processes/[id]";
import { POST as deployRoute } from "@/pages/api/processes/[id]/revisions";
import { POST as createSourceRoute } from "@/pages/api/processes/[id]/sources/index";
import { POST as createOutputRoute } from "@/pages/api/processes/[id]/outputs/index";
import { POST as testRoute } from "@/pages/api/processes/[id]/test";
import { GET as pollRoute } from "@/pages/api/processes/[id]/checks/[checkId]";

const PROCESS_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const REVISION_ID = "3a9f1c2e-0000-4000-8000-0000000000b1";
const CHECK_ID = "3a9f1c2e-0000-4000-8000-0000000000c1";
const EO = "earth-observation";
const OTHER = "other-group";

function process(overrides: Partial<ApiProcess> = {}): ApiProcess {
  return {
    id: PROCESS_ID,
    name: "cloud-mask",
    description: "",
    group_id: EO,
    current_revision: REVISION_ID,
    enabled: true,
    max_runs_per_hour: 60,
    created_by: "user-1",
    created_at: "2026-08-30T00:00:00.000Z",
    updated_at: "2026-08-30T00:00:00.000Z",
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

const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };
const operator = authed(["operator"]);
const member = authed(["member"]);
const admin = authed(["admin"], []);

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  {
    method = "GET",
    body,
    params = {},
  }: { method?: string; body?: unknown; params?: Record<string, string> } = {},
) {
  const url = new URL("http://localhost:4321/api/processes");
  return handler({
    url,
    locals: { auth },
    request: new Request(url, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    params: { id: PROCESS_ID, ...params },
  } as never);
}

const INLINE = { kind: "inline_python" as const };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getProcess).mockResolvedValue(process());
  vi.mocked(canManageCollection).mockResolvedValue(true);
  vi.mocked(getCollectionSettings).mockResolvedValue({
    archived: false,
  } as never);
});

describe("GET /api/processes", () => {
  it("401s anonymously", async () => {
    expect((await call(listRoute, anon)).status).toBe(401);
  });

  it("scopes a member to their own groups", async () => {
    vi.mocked(listProcesses).mockResolvedValue([]);
    await call(listRoute, member);
    expect(listProcesses).toHaveBeenCalledWith([EO]);
  });

  it("passes null for an admin — all groups", async () => {
    vi.mocked(listProcesses).mockResolvedValue([]);
    await call(listRoute, admin);
    expect(listProcesses).toHaveBeenCalledWith(null);
  });
});

describe("POST /api/processes", () => {
  it("403s a member (operator+ to create)", async () => {
    const res = await call(createRoute, member, {
      method: "POST",
      body: { name: "p", group_id: EO },
    });
    expect(res.status).toBe(403);
    expect(createProcess).not.toHaveBeenCalled();
  });

  it("403s creating into a group the operator is not in", async () => {
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "p", group_id: OTHER },
    });
    expect(res.status).toBe(403);
    expect(createProcess).not.toHaveBeenCalled();
  });

  it("applies the §7 ceiling default and creates", async () => {
    vi.mocked(createProcess).mockResolvedValue(process());
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "cloud-mask", group_id: EO },
    });
    expect(res.status).toBe(201);
    expect(createProcess).toHaveBeenCalledWith(
      expect.objectContaining({ maxRunsPerHour: 60, groupId: EO }),
    );
  });

  it("400s a ceiling of zero — 'unlimited' is not expressible", async () => {
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "p", group_id: EO, max_runs_per_hour: 0 },
    });
    expect(res.status).toBe(400);
  });

  it("400s an unknown field (strict write gate)", async () => {
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "p", group_id: EO, current_revision: REVISION_ID },
    });
    expect(res.status).toBe(400);
  });
});

describe("GET/PUT/DELETE /api/processes/[id]", () => {
  it("404s a process outside the caller's groups — existence is scoped", async () => {
    vi.mocked(getProcess).mockResolvedValue(process({ group_id: OTHER }));
    expect((await call(getRoute, member)).status).toBe(404);
  });

  it("lets a member read their own group's process", async () => {
    expect((await call(getRoute, member)).status).toBe(200);
  });

  it("403s a member updating", async () => {
    const res = await call(updateRoute, member, {
      method: "PUT",
      body: { enabled: false },
    });
    expect(res.status).toBe(403);
    expect(updateProcess).not.toHaveBeenCalled();
  });

  it("refuses to repoint current_revision through an ordinary update", async () => {
    // Only a deploy moves it — an edit must never silently change what runs.
    const res = await call(updateRoute, operator, {
      method: "PUT",
      body: { current_revision: REVISION_ID },
    });
    expect(res.status).toBe(400);
    expect(updateProcess).not.toHaveBeenCalled();
  });

  it("403s re-homing into a group the caller is not in", async () => {
    const res = await call(updateRoute, operator, {
      method: "PUT",
      body: { group_id: OTHER },
    });
    expect(res.status).toBe(403);
    expect(updateProcess).not.toHaveBeenCalled();
  });

  it("soft-deletes (ADR 0009) rather than removing history", async () => {
    vi.mocked(softDeleteProcess).mockResolvedValue(true);
    const res = await call(deleteRoute, operator, { method: "DELETE" });
    expect(res.status).toBe(200);
    expect(softDeleteProcess).toHaveBeenCalledWith(PROCESS_ID);
  });
});

describe("POST /api/processes/[id]/revisions (deploy)", () => {
  it("refuses the container runtime this slice (spec §4, ADR 0013)", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: {
        runtime: { kind: "container", image: "ghcr.io/example/p:1" },
        code: null,
      },
    });
    expect(res.status).toBe(400);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("400s an inline_python revision with no code", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: INLINE, code: "   " },
    });
    expect(res.status).toBe(400);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("deploys, defaulting env to empty", async () => {
    vi.mocked(deployRevision).mockResolvedValue({
      id: REVISION_ID,
      process_id: PROCESS_ID,
      runtime: INLINE,
      code: "print(1)",
      env: [],
      created_by: "user-1",
      created_at: "2026-08-30T00:00:00.000Z",
    });
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: INLINE, code: "print(1)" },
    });
    expect(res.status).toBe(201);
    expect(deployRevision).toHaveBeenCalledWith(
      expect.objectContaining({ processId: PROCESS_ID, env: [] }),
    );
  });

  it("400s an env entry carrying both a value and a secret_ref", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: {
        runtime: INLINE,
        code: "print(1)",
        env: [
          {
            name: "T",
            value: "hunter2",
            secret_ref: { connection_id: CHECK_ID, key: "password" },
          },
        ],
      },
    });
    expect(res.status).toBe(400);
    expect(deployRevision).not.toHaveBeenCalled();
  });
});

describe("POST /api/processes/[id]/sources and /outputs", () => {
  it("403s when the caller cannot manage the collection", async () => {
    vi.mocked(canManageCollection).mockResolvedValue(false);
    const res = await call(createSourceRoute, operator, {
      method: "POST",
      body: { collection_id: "sentinel-2", trigger: { kind: "item_event" } },
    });
    expect(res.status).toBe(403);
    expect(createSource).not.toHaveBeenCalled();
  });

  it("409s a source on an archived collection (M2-F)", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      archived: true,
    } as never);
    const res = await call(createSourceRoute, operator, {
      method: "POST",
      body: { collection_id: "sentinel-2", trigger: { kind: "item_event" } },
    });
    expect(res.status).toBe(409);
    expect(createSource).not.toHaveBeenCalled();
  });

  it("400s a cron trigger with a non-cron schedule", async () => {
    const res = await call(createSourceRoute, operator, {
      method: "POST",
      body: {
        collection_id: "sentinel-2",
        trigger: { kind: "cron", schedule: "every 15 minutes" },
      },
    });
    expect(res.status).toBe(400);
  });

  it("creates a source with a null expectation by default", async () => {
    vi.mocked(createSource).mockResolvedValue({} as never);
    const res = await call(createSourceRoute, operator, {
      method: "POST",
      body: { collection_id: "sentinel-2", trigger: { kind: "item_event" } },
    });
    expect(res.status).toBe(201);
    expect(createSource).toHaveBeenCalledWith(
      expect.objectContaining({ expectation: null, enabled: true }),
    );
  });

  it("409s an output on an archived collection — outputs WRITE items", async () => {
    vi.mocked(getCollectionSettings).mockResolvedValue({
      archived: true,
    } as never);
    const res = await call(createOutputRoute, operator, {
      method: "POST",
      body: { collection_id: "cloud-masks" },
    });
    expect(res.status).toBe(409);
    expect(createOutput).not.toHaveBeenCalled();
  });
});

describe("the ADR 0004 test-run bridge", () => {
  it("409s a process with nothing deployed rather than queueing forever", async () => {
    vi.mocked(getProcess).mockResolvedValue(
      process({ current_revision: null }),
    );
    const res = await call(testRoute, operator, { method: "POST" });
    expect(res.status).toBe(409);
    expect(insertProcessCheck).not.toHaveBeenCalled();
  });

  it("403s a member requesting a test run", async () => {
    const res = await call(testRoute, member, { method: "POST" });
    expect(res.status).toBe(403);
    expect(insertProcessCheck).not.toHaveBeenCalled();
  });

  it("inserts a request against the CURRENT revision and 202s", async () => {
    // The app never executes anything — it writes a request row the pipeline
    // drains (ADR 0004), so the response is Accepted, not Created.
    vi.mocked(insertProcessCheck).mockResolvedValue({} as never);
    const res = await call(testRoute, operator, { method: "POST" });
    expect(res.status).toBe(202);
    expect(insertProcessCheck).toHaveBeenCalledWith(
      PROCESS_ID,
      REVISION_ID,
      "user-1",
    );
  });

  it("lets a member poll, and 404s an unknown check", async () => {
    vi.mocked(getProcessCheck).mockResolvedValue(null);
    const res = await call(pollRoute, member, { params: { checkId: CHECK_ID } });
    expect(res.status).toBe(404);
  });

  it("404s a malformed check id before it reaches a uuid column", async () => {
    const res = await call(pollRoute, member, {
      params: { checkId: "not-a-uuid" },
    });
    expect(res.status).toBe(404);
    expect(getProcessCheck).not.toHaveBeenCalled();
  });
});
