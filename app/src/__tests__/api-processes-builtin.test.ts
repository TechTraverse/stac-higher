// @vitest-environment node
/**
 * X-4: the built-in extractor library's app surface (X-queue spec §7).
 *
 *   GET  /api/extractors/builtin       — the registry (member+)
 *   POST /api/processes/builtin        — create-or-reuse a group's process
 *   POST /api/processes/[id]/revisions — a built-in process refuses a code
 *                                        deploy and accepts "Update to current"
 */
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/processes/storage", () => ({
  getProcess: vi.fn(),
  findBuiltinProcess: vi.fn(),
  createBuiltinProcess: vi.fn(),
  deployRevision: vi.fn(),
  listRevisions: vi.fn(),
  DuplicateProcessNameError: class DuplicateProcessNameError extends Error {},
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));
vi.mock("@/lib/connections/storage", () => ({ getConnection: vi.fn() }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import {
  createBuiltinProcess,
  deployRevision,
  DuplicateProcessNameError,
  findBuiltinProcess,
  getProcess,
  type ApiProcess,
} from "@/lib/processes/storage";
import {
  BUILTIN_CODE_DEPLOY_REFUSAL,
  BUILTIN_REGISTRY_DRIFT,
  BUILTIN_TEMPLATE_ONLY_FOR_BUILTIN,
} from "@/lib/processes/schemas";
import { builtinProcessBody } from "@/lib/extractors/template";
import { resetHardwareProfilesCache } from "@/lib/processes/hardware";
import { GET as registryRoute } from "@/pages/api/extractors/builtin";
import { POST as builtinRoute } from "@/pages/api/processes/builtin";
import { POST as deployRoute } from "@/pages/api/processes/[id]/revisions";

// K-1 final review Item 1: a profile set whose 'standard' ceiling is below
// the registry template's memory_mb (stactools-goes = 2048), to prove both
// built-in deploy paths run the same write gate the hand-written path does.
function writeLowMemoryProfiles(): string {
  const dir = mkdtempSync(join(tmpdir(), "hardware-profiles-"));
  const path = join(dir, "low-memory.json");
  writeFileSync(
    path,
    JSON.stringify({
      version: 1,
      profiles: [
        {
          id: "standard",
          label: "Standard",
          description: "test-only, memory capped below the registry template",
          tier: "cpu",
          accelerator: null,
          cpu: { min: 0.25, max: 4, default: 1 },
          memory_mb: { min: 128, max: 1024, default: 512 },
          gpu_count: null,
          max_queue_wait_seconds: 1800,
          image: null,
          backend: {},
        },
      ],
    }),
  );
  return path;
}

const EO = "eo-team";
const PROCESS_ID = "5c9f1c2e-0000-4000-8000-0000000000e1";
const REVISION_ID = "5c9f1c2e-0000-4000-8000-0000000000f1";

function process(overrides: Partial<ApiProcess> = {}): ApiProcess {
  return {
    id: PROCESS_ID,
    name: "GOES-R ABI (L1b / L2)",
    description: "",
    group_id: EO,
    kind: "extractor",
    current_revision: REVISION_ID,
    enabled: true,
    max_runs_per_hour: 600,
    builtin_id: "stactools-goes",
    created_by: "user-1",
    created_at: "2026-09-04T00:00:00.000Z",
    updated_at: "2026-09-04T00:00:00.000Z",
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

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  { method = "GET", body }: { method?: string; body?: unknown } = {},
) {
  const url = new URL("http://localhost:4321/api/processes/builtin");
  return handler({
    url,
    locals: { auth },
    request: new Request(url, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    params: { id: PROCESS_ID },
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  delete globalThis.process.env.PROCESS_HARDWARE_PROFILES_FILE;
  resetHardwareProfilesCache();
});

describe("GET /api/extractors/builtin", () => {
  it("serves the registry the app was built with, to any authenticated user", async () => {
    const res = await call(registryRoute, member);
    expect(res.status).toBe(200);
    const body = (await res.json()) as { extractors: { id: string; adapter: string }[] };
    expect(body.extractors).toHaveLength(11);
    expect(body.extractors.map((e) => e.id)).toContain("stactools-goes");
    expect(body.extractors.every((e) => typeof e.adapter === "string")).toBe(true);
  });

  it("requires authentication", async () => {
    expect((await call(registryRoute, anon)).status).toBe(401);
  });
});

describe("POST /api/processes/builtin (create-or-reuse)", () => {
  it("creates the group's process from the template, revision 1 included, on first pick", async () => {
    vi.mocked(findBuiltinProcess).mockResolvedValue(null);
    vi.mocked(createBuiltinProcess).mockResolvedValue(process());
    const res = await call(builtinRoute, operator, {
      method: "POST",
      body: { builtin_id: "stactools-goes", group_id: EO },
    });
    expect(res.status).toBe(201);
    expect(((await res.json()) as ApiProcess).builtin_id).toBe("stactools-goes");
    const input = vi.mocked(createBuiltinProcess).mock.calls[0][0];
    expect(input).toMatchObject({
      groupId: EO,
      builtinId: "stactools-goes",
      name: "GOES-R ABI (L1b / L2)",
      maxRunsPerHour: 600,
      createdBy: "user-1",
    });
    expect(input.revision.code).toBe(builtinProcessBody({ id: "stactools-goes" } as never));
    expect(input.revision.runtime).toMatchObject({
      kind: "inline_python",
      image: null,
      runtime_image: "stactools",
      memory_mb: 2048,
      timeout_seconds: 300,
      network: { level: "isolated", hosts: [] },
    });
    expect(input.revision.env).toEqual([]);
  });

  it("reuses the group's live process on a second pick — idempotent per group", async () => {
    vi.mocked(findBuiltinProcess).mockResolvedValue(process());
    const res = await call(builtinRoute, operator, {
      method: "POST",
      body: { builtin_id: "stactools-goes", group_id: EO },
    });
    expect(res.status).toBe(200);
    expect(((await res.json()) as ApiProcess).id).toBe(PROCESS_ID);
    expect(createBuiltinProcess).not.toHaveBeenCalled();
  });

  it("converges on the winner when two picks race", async () => {
    vi.mocked(findBuiltinProcess).mockResolvedValueOnce(null).mockResolvedValueOnce(process());
    vi.mocked(createBuiltinProcess).mockResolvedValue(null); // lost the unique index
    const res = await call(builtinRoute, operator, {
      method: "POST",
      body: { builtin_id: "stactools-goes", group_id: EO },
    });
    expect(res.status).toBe(200);
    expect(((await res.json()) as ApiProcess).id).toBe(PROCESS_ID);
  });

  it("refuses an id that is not in the registry", async () => {
    const res = await call(builtinRoute, operator, {
      method: "POST",
      body: { builtin_id: "stactools-nope", group_id: EO },
    });
    expect(res.status).toBe(404);
    expect(findBuiltinProcess).not.toHaveBeenCalled();
  });

  it("is operator-only and group-scoped, with a strict body", async () => {
    expect(
      (await call(builtinRoute, member, {
        method: "POST",
        body: { builtin_id: "stactools-goes", group_id: EO },
      })).status,
    ).toBe(403);
    expect(
      (await call(builtinRoute, operator, {
        method: "POST",
        body: { builtin_id: "stactools-goes", group_id: "not-mine" },
      })).status,
    ).toBe(403);
    expect(
      (await call(builtinRoute, operator, {
        method: "POST",
        body: { builtin_id: "stactools-goes", group_id: EO, name: "x" },
      })).status,
    ).toBe(400);
    expect((await call(builtinRoute, anon, { method: "POST", body: {} })).status).toBe(401);
  });

  it("refuses to create a process when the registry template outgrows the deployment's profile (K-1 final review Item 1)", async () => {
    globalThis.process.env.PROCESS_HARDWARE_PROFILES_FILE = writeLowMemoryProfiles();
    resetHardwareProfilesCache();
    vi.mocked(findBuiltinProcess).mockResolvedValue(null);
    const res = await call(builtinRoute, operator, {
      method: "POST",
      body: { builtin_id: "stactools-goes", group_id: EO },
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe(
      "memory_mb 2048 is outside profile 'standard' bounds 128–1024",
    );
    expect(createBuiltinProcess).not.toHaveBeenCalled();
  });

  it("names a name collision with a hand-written process as a 409", async () => {
    vi.mocked(findBuiltinProcess).mockResolvedValue(null);
    vi.mocked(createBuiltinProcess).mockRejectedValue(
      new DuplicateProcessNameError("GOES-R ABI (L1b / L2)", EO),
    );
    const res = await call(builtinRoute, operator, {
      method: "POST",
      body: { builtin_id: "stactools-goes", group_id: EO },
    });
    expect(res.status).toBe(409);
  });
});

describe("POST /api/processes/[id]/revisions on a built-in process", () => {
  it("refuses a code deploy — the code is read-only", async () => {
    vi.mocked(getProcess).mockResolvedValue(process());
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { runtime: { kind: "inline_python" }, code: "print(1)" },
    });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe(BUILTIN_CODE_DEPLOY_REFUSAL);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("'Update to current' deploys a fresh revision from the registry template", async () => {
    vi.mocked(getProcess).mockResolvedValue(process());
    vi.mocked(deployRevision).mockResolvedValue({
      id: "5c9f1c2e-0000-4000-8000-0000000000f2",
      process_id: PROCESS_ID,
      runtime: {},
      code: "",
      env: [],
      created_by: "user-1",
      created_at: "2026-09-04T00:00:00.000Z",
    });
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { from_builtin: true },
    });
    expect(res.status).toBe(201);
    const input = vi.mocked(deployRevision).mock.calls[0][0];
    expect(input.processId).toBe(PROCESS_ID);
    expect(input.code).toContain('run("stactools-goes")');
    expect(input.runtime).toMatchObject({ runtime_image: "stactools", kind: "inline_python" });
  });

  it("refuses 'Update to current' when the registry template outgrows the deployment's profile (K-1 final review Item 1)", async () => {
    globalThis.process.env.PROCESS_HARDWARE_PROFILES_FILE = writeLowMemoryProfiles();
    resetHardwareProfilesCache();
    vi.mocked(getProcess).mockResolvedValue(process());
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { from_builtin: true },
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe(
      "memory_mb 2048 is outside profile 'standard' bounds 128–1024",
    );
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("cannot update a process whose registry entry is gone", async () => {
    vi.mocked(getProcess).mockResolvedValue(process({ builtin_id: "stactools-gone" }));
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { from_builtin: true },
    });
    expect(res.status).toBe(409);
    expect(((await res.json()) as { error: string }).error).toBe(BUILTIN_REGISTRY_DRIFT);
    expect(deployRevision).not.toHaveBeenCalled();
  });

  it("from_builtin means nothing on a hand-written process", async () => {
    vi.mocked(getProcess).mockResolvedValue(process({ builtin_id: null }));
    const res = await call(deployRoute, operator, {
      method: "POST",
      body: { from_builtin: true },
    });
    expect(res.status).toBe(400);
    expect(((await res.json()) as { error: string }).error).toBe(
      BUILTIN_TEMPLATE_ONLY_FOR_BUILTIN,
    );
  });
});
