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
  listRuns: vi.fn(),
  getRun: vi.fn(),
  rerunRun: vi.fn(),
  DuplicateProcessNameError: class extends Error {},
  DuplicateSourceError: class extends Error {},
  DuplicateOutputError: class extends Error {},
}));
vi.mock("@/lib/associations/access", () => ({
  canManageCollection: vi.fn(async () => true),
}));
vi.mock("@/lib/associations/storage", () => ({
  countAssociationsUsingExtractor: vi.fn(async () => 0),
}));
vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(async () => ({ archived: false })),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));
vi.mock("@/lib/graph/storage", () => ({ loadGraphEdges: vi.fn(async () => []) }));
vi.mock("@/lib/connections/storage", () => ({ getConnection: vi.fn() }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { canManageCollection } from "@/lib/associations/access";
import { countAssociationsUsingExtractor } from "@/lib/associations/storage";
import { loadGraphEdges } from "@/lib/graph/storage";
import { collectionNode, processNode } from "@/lib/graph/edges";
import { getCollectionSettings } from "@/lib/collections/settings";
import { getConnection } from "@/lib/connections/storage";
import {
  createOutput,
  createProcess,
  createSource,
  deployRevision,
  getProcess,
  getProcessCheck,
  insertProcessCheck,
  listProcesses,
  listRuns,
  getRun,
  rerunRun,
  softDeleteProcess,
  updateProcess,
  type ApiProcess,
  type ApiProcessRun,
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
import { NETWORK_LEVEL_NOT_YET_AVAILABLE } from "@/lib/processes/schemas";
import { POST as deployRoute } from "@/pages/api/processes/[id]/revisions";
import { POST as createSourceRoute } from "@/pages/api/processes/[id]/sources/index";
import { POST as createOutputRoute } from "@/pages/api/processes/[id]/outputs/index";
import { POST as testRoute } from "@/pages/api/processes/[id]/test";
import { GET as pollRoute } from "@/pages/api/processes/[id]/checks/[checkId]";
import { GET as runsRoute } from "@/pages/api/processes/[id]/runs/index";
import { POST as rerunRoute } from "@/pages/api/processes/[id]/runs/[runId]/rerun";
import { GET as hardwareProfilesRoute } from "@/pages/api/processes/hardware-profiles";

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
    kind: "transform",
    current_revision: REVISION_ID,
    enabled: true,
    max_runs_per_hour: 60,
    builtin_id: null,
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
const RUN_ID = "3a9f1c2e-0000-4000-8000-0000000000d1";

function run(overrides: Partial<ApiProcessRun> = {}): ApiProcessRun {
  return {
    id: RUN_ID,
    process_id: PROCESS_ID,
    revision_id: REVISION_ID,
    source_id: null,
    status: "dead",
    attempts: 3,
    input_items: [],
    output_items: [],
    log_ref: null,
    error: "run exited 1",
    rate_deferred_until: null,
    is_test: false,
    created_at: "2026-08-31T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getProcess).mockResolvedValue(process());
  vi.mocked(canManageCollection).mockResolvedValue(true);
  vi.mocked(getCollectionSettings).mockResolvedValue({
    archived: false,
  } as never);
  vi.mocked(loadGraphEdges).mockResolvedValue([]);
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

  it("deploys an explicitly isolated network profile (GOES spec §4)", async () => {
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
      body: {
        runtime: { ...INLINE, network: { level: "isolated", hosts: [] } },
        code: "print(1)",
      },
    });
    expect(res.status).toBe(201);
    const stored = vi.mocked(deployRevision).mock.calls[0][0].runtime as {
      network: unknown;
    };
    expect(stored.network).toEqual({ level: "isolated", hosts: [] });
  });

  it("refuses a network level above isolated this slice", async () => {
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { ...INLINE, network: { level: "open" } }, code: "print(1)" },
    });
    expect(res.status).toBe(400);
    // Slice 1: the WRITE GATE refuses first, with its own message. Once the
    // gate opens (egress proxy, GOES spec §11) the PROCESS_NETWORK_MAX cap
    // message from `@/lib/processes/network` becomes the reachable refusal.
    const body = (await res.json()) as { details: { message: string }[] };
    expect(body.details.map((d) => d.message)).toContain(NETWORK_LEVEL_NOT_YET_AVAILABLE);
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

/**
 * A `secret_ref` is a pointer into another row's write-only credentials, so
 * the deploy verb is the last place the platform can check that the pointer
 * stays inside the process's own group. The revision outlives the operator
 * who deployed it, so the test is against the PROCESS's group, not the
 * caller's — an admin deploying into someone else's process must not be able
 * to widen its reach either.
 */
describe("POST /api/processes/[id]/revisions — secret_ref scoping", () => {
  const CONNECTION = "3a9f1c2e-0000-4000-8000-0000000000e1";

  function envRef() {
    return [{ name: "SOURCE_TOKEN", secret_ref: { connection_id: CONNECTION, key: "password" } }];
  }

  function conn(groupId: string) {
    return { id: CONNECTION, group_id: groupId } as never;
  }

  it("deploys a secret_ref that points inside the process's group", async () => {
    vi.mocked(getConnection).mockResolvedValue(conn(EO));
    vi.mocked(deployRevision).mockResolvedValue({
      id: REVISION_ID,
      process_id: PROCESS_ID,
      runtime: INLINE,
      code: "print(1)",
      env: envRef(),
      created_by: "user-1",
      created_at: "2026-08-30T00:00:00.000Z",
    });
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: INLINE, code: "print(1)", env: envRef() },
    });
    expect(res.status).toBe(201);
  });

  it("400s a secret_ref pointing at a connection in another group", async () => {
    vi.mocked(getConnection).mockResolvedValue(conn(OTHER));
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: INLINE, code: "print(1)", env: envRef() },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toMatch(/SOURCE_TOKEN/);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("gives a missing connection the same answer as a foreign one", async () => {
    // Distinguishing them would turn the deploy form into an oracle for
    // which connection UUIDs exist in groups the caller cannot see.
    vi.mocked(getConnection).mockResolvedValue(conn(OTHER));
    const foreign = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: INLINE, code: "print(1)", env: envRef() },
    });
    vi.mocked(getConnection).mockResolvedValue(null);
    const missing = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: INLINE, code: "print(1)", env: envRef() },
    });
    expect(missing.status).toBe(foreign.status);
    expect(await missing.json()).toEqual(await foreign.json());
  });

  it("checks the PROCESS's group, not the admin caller's", async () => {
    vi.mocked(getConnection).mockResolvedValue(conn(OTHER));
    const res = await call(deployRoute, admin, {
      method: "POST",
      body: { runtime: INLINE, code: "print(1)", env: envRef() },
    });
    expect(res.status).toBe(400);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("never touches connections for a literal-only env", async () => {
    vi.mocked(deployRevision).mockResolvedValue({
      id: REVISION_ID,
      process_id: PROCESS_ID,
      runtime: INLINE,
      code: "print(1)",
      env: [{ name: "TILE_SIZE", value: "512" }],
      created_by: "user-1",
      created_at: "2026-08-30T00:00:00.000Z",
    });
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: {
        runtime: INLINE,
        code: "print(1)",
        env: [{ name: "TILE_SIZE", value: "512" }],
      },
    });
    expect(res.status).toBe(201);
    expect(getConnection).not.toHaveBeenCalled();
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

describe("the run ledger and the re-run verb (M5-C)", () => {
  it("lets a member read the ledger", async () => {
    vi.mocked(listRuns).mockResolvedValue([run()]);
    const res = await call(runsRoute, member, { params: { runId: RUN_ID } });
    expect(res.status).toBe(200);
    expect(listRuns).toHaveBeenCalledWith(PROCESS_ID, 50);
  });

  it("403s a member re-running", async () => {
    const res = await call(rerunRoute, member, {
      method: "POST",
      params: { runId: RUN_ID },
    });
    expect(res.status).toBe(403);
    expect(rerunRun).not.toHaveBeenCalled();
  });

  it("requeues a dead run and 202s — the app executes nothing", async () => {
    vi.mocked(rerunRun).mockResolvedValue(run({ status: "queued", attempts: 0 }));
    const res = await call(rerunRoute, operator, {
      method: "POST",
      params: { runId: RUN_ID },
    });
    expect(res.status).toBe(202);
    expect(rerunRun).toHaveBeenCalledWith(PROCESS_ID, RUN_ID);
  });

  it("409s a non-dead run WITH its status, rather than silently doing nothing", async () => {
    // The conditional UPDATE matched nothing; an operator recovering a flow
    // needs to know why, not to wonder whether the click registered.
    vi.mocked(rerunRun).mockResolvedValue(null);
    vi.mocked(getRun).mockResolvedValue(run({ status: "running" }));
    const res = await call(rerunRoute, operator, {
      method: "POST",
      params: { runId: RUN_ID },
    });
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("running");
  });

  it("404s an unknown run", async () => {
    vi.mocked(rerunRun).mockResolvedValue(null);
    vi.mocked(getRun).mockResolvedValue(null);
    const res = await call(rerunRoute, operator, {
      method: "POST",
      params: { runId: RUN_ID },
    });
    expect(res.status).toBe(404);
  });

  it("404s a malformed run id before it reaches a uuid column", async () => {
    const res = await call(rerunRoute, operator, {
      method: "POST",
      params: { runId: "nope" },
    });
    expect(res.status).toBe(404);
    expect(rerunRun).not.toHaveBeenCalled();
  });

  it("409s re-running an EXTRACTOR run — the ingest sweep owns that retry", async () => {
    // An extractor run's rows are re-driven by the ingest failed-retry sweep.
    // Flipping the row back to `queued` either collides with the newer queued
    // run for the same association (23505 -> 500) or re-executes against rows
    // it no longer owns.
    vi.mocked(getProcess).mockResolvedValue(process({ kind: "extractor" }));
    const res = await call(rerunRoute, operator, {
      method: "POST",
      params: { runId: RUN_ID },
    });
    expect(res.status).toBe(409);
    expect((await res.json()).error).toContain("extractor run");
    expect(rerunRun).not.toHaveBeenCalled();
  });
});

describe("spec §6.5 kind-aware defaults", () => {
  it("gives an extractor created through the API the 600/h ceiling", async () => {
    vi.mocked(createProcess).mockResolvedValue(process({ kind: "extractor" }));
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "goes-extract", group_id: EO, kind: "extractor" },
    });
    expect(res.status).toBe(201);
    expect(vi.mocked(createProcess).mock.calls[0][0]).toMatchObject({
      kind: "extractor",
      maxRunsPerHour: 600,
    });
  });

  it("leaves a transform at 60/h", async () => {
    vi.mocked(createProcess).mockResolvedValue(process());
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "cloud-mask", group_id: EO },
    });
    expect(res.status).toBe(201);
    expect(vi.mocked(createProcess).mock.calls[0][0]).toMatchObject({
      kind: "transform",
      maxRunsPerHour: 60,
    });
  });

  it("still honours an explicit ceiling", async () => {
    vi.mocked(createProcess).mockResolvedValue(process({ kind: "extractor" }));
    await call(createRoute, operator, {
      method: "POST",
      body: {
        name: "goes-extract",
        group_id: EO,
        kind: "extractor",
        max_runs_per_hour: 5,
      },
    });
    expect(vi.mocked(createProcess).mock.calls[0][0]).toMatchObject({
      maxRunsPerHour: 5,
    });
  });
});

describe("cycle refusal (I-64, M5-D)", () => {
  it("409s attaching an output the process already sources from", async () => {
    // p1 already reads cloud-masks; publishing back into it would make the
    // process re-trigger on its own output forever.
    vi.mocked(loadGraphEdges).mockResolvedValue([
      {
        from: collectionNode("cloud-masks"),
        to: processNode(PROCESS_ID),
        kind: "process_source",
        id: "e1",
      },
    ]);
    const res = await call(createOutputRoute, operator, {
      method: "POST",
      body: { collection_id: "cloud-masks" },
    });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toContain("processing loop");
    // The PATH is in the body: an operator needs to know which wiring to
    // undo, not just that something was refused.
    expect(body.cycle).toEqual([
      processNode(PROCESS_ID),
      collectionNode("cloud-masks"),
      processNode(PROCESS_ID),
    ]);
    expect(createOutput).not.toHaveBeenCalled();
  });

  it("409s attaching a source the process already outputs to", async () => {
    vi.mocked(loadGraphEdges).mockResolvedValue([
      {
        from: processNode(PROCESS_ID),
        to: collectionNode("sentinel-2"),
        kind: "process_output",
        id: "e1",
      },
    ]);
    const res = await call(createSourceRoute, operator, {
      method: "POST",
      body: { collection_id: "sentinel-2", trigger: { kind: "item_event" } },
    });
    expect(res.status).toBe(409);
    expect(createSource).not.toHaveBeenCalled();
  });

  it("permits an ordinary source → process → different collection wiring", async () => {
    vi.mocked(createOutput).mockResolvedValue({} as never);
    vi.mocked(loadGraphEdges).mockResolvedValue([
      {
        from: collectionNode("sentinel-2"),
        to: processNode(PROCESS_ID),
        kind: "process_source",
        id: "e1",
      },
    ]);
    const res = await call(createOutputRoute, operator, {
      method: "POST",
      body: { collection_id: "cloud-masks" },
    });
    expect(res.status).toBe(201);
  });
});

describe("process kind (G-6)", () => {
  it("creates a transform by default and passes an explicit extractor through", async () => {
    vi.mocked(createProcess).mockImplementation(async (input) =>
      ({ ...process(), kind: input.kind }) as ApiProcess,
    );
    const a = await call(createRoute, operator, {
      method: "POST",
      body: { name: "x", group_id: EO },
    });
    expect(a.status).toBe(201);
    expect(vi.mocked(createProcess).mock.calls[0][0].kind).toBe("transform");

    const b = await call(createRoute, operator, {
      method: "POST",
      body: { name: "y", group_id: EO, kind: "extractor" },
    });
    expect(b.status).toBe(201);
    expect(vi.mocked(createProcess).mock.calls[1][0].kind).toBe("extractor");
  });

  it("refuses an unknown kind", async () => {
    const res = await call(createRoute, operator, {
      method: "POST",
      body: { name: "z", group_id: EO, kind: "filter" },
    });
    expect(res.status).toBe(400);
  });

  it("refuses sources and outputs on an extractor with a 409 naming the kind", async () => {
    vi.mocked(getProcess).mockResolvedValue({ ...process(), kind: "extractor" });
    const src = await call(createSourceRoute, operator, {
      method: "POST",
      body: {
        collection_id: "c",
        trigger: { kind: "item_event", item_filter: null },
      },
    });
    expect(src.status).toBe(409);
    expect((await src.json()).error).toMatch(/extractor/);
    expect(createSource).not.toHaveBeenCalled();

    const out = await call(createOutputRoute, operator, {
      method: "POST",
      body: { collection_id: "c" },
    });
    expect(out.status).toBe(409);
    expect(createOutput).not.toHaveBeenCalled();
  });

  it("refuses deleting an extractor an association still names", async () => {
    vi.mocked(getProcess).mockResolvedValue({ ...process(), kind: "extractor" });
    vi.mocked(countAssociationsUsingExtractor).mockResolvedValue(2);
    const res = await call(deleteRoute, operator, { method: "DELETE" });
    expect(res.status).toBe(409);
    expect((await res.json()).error).toMatch(/2 ingest association/);
    expect(softDeleteProcess).not.toHaveBeenCalled();
  });
});

describe("GET /api/processes/hardware-profiles (K-1)", () => {
  it("lists the deployment's profiles without their backend blocks, member+", async () => {
    const res = await call(hardwareProfilesRoute, member);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.backend).toBe("docker");
    expect(body.profiles.map((p: { id: string }) => p.id)).toContain("standard");
    expect(body.profiles.every((p: object) => !("backend" in p))).toBe(true);
  });

  it("requires authentication", async () => {
    const res = await call(hardwareProfilesRoute, anon);
    expect(res.status).toBe(401);
  });
});
