// @vitest-environment node
/**
 * /api/monitoring/history + the run-log route (M5-F): validation, the
 * server-side window clamp, and group scoping.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/monitoring/history", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/monitoring/history")>();
  return { ...actual, listDailyStats: vi.fn() };
});
vi.mock("@/lib/processes/storage", () => ({
  getProcess: vi.fn(),
  getRun: vi.fn(),
}));
vi.mock("@/lib/storage/presign", () => ({
  presignGetUrl: vi.fn(async () => "https://storage.example/signed"),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { listDailyStats } from "@/lib/monitoring/history";
import { getProcess, getRun } from "@/lib/processes/storage";
import { presignGetUrl } from "@/lib/storage/presign";
import { GET as historyRoute } from "@/pages/api/monitoring/history";
import { GET as logRoute } from "@/pages/api/processes/[id]/runs/[runId]/log";

const EO = "earth-observation";
const PROCESS_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const RUN_ID = "3a9f1c2e-0000-4000-8000-0000000000d1";
const SUBJECT = "3a9f1c2e-0000-4000-8000-0000000000e1";

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

type Handler = (ctx: never) => Promise<Response>;

function callHistory(auth: AuthContext, qs: string) {
  const url = new URL(`http://localhost:4321/api/monitoring/history?${qs}`);
  return (historyRoute as unknown as Handler)({
    url,
    locals: { auth },
    request: new Request(url),
    params: {},
  } as never);
}

function callLog(auth: AuthContext, runId = RUN_ID) {
  const url = new URL("http://localhost:4321/api/processes/x/runs/y/log");
  return (logRoute as unknown as Handler)({
    url,
    locals: { auth },
    request: new Request(url),
    params: { id: PROCESS_ID, runId },
  } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listDailyStats).mockResolvedValue([]);
  vi.mocked(getProcess).mockResolvedValue({
    id: PROCESS_ID,
    group_id: EO,
  } as never);
});

describe("GET /api/monitoring/history", () => {
  it("401s anonymously", async () => {
    expect((await callHistory(anon, "")).status).toBe(401);
  });

  it("400s an unknown subject kind", async () => {
    const res = await callHistory(
      authed(["member"]),
      `subject_kind=collection&subject_id=${SUBJECT}`,
    );
    expect(res.status).toBe(400);
  });

  it("400s a non-uuid subject id before it reaches a uuid column", async () => {
    const res = await callHistory(
      authed(["member"]),
      "subject_kind=process&subject_id=nope",
    );
    expect(res.status).toBe(400);
    expect(listDailyStats).not.toHaveBeenCalled();
  });

  it("clamps the window server-side — one page view cannot scan the archive", async () => {
    await callHistory(
      authed(["member"]),
      `subject_kind=process&subject_id=${SUBJECT}&days=99999`,
    );
    expect(listDailyStats).toHaveBeenCalledWith("process", SUBJECT, 400, [EO]);
  });

  it("floors the window at one day", async () => {
    await callHistory(
      authed(["member"]),
      `subject_kind=process&subject_id=${SUBJECT}&days=0`,
    );
    expect(listDailyStats).toHaveBeenCalledWith("process", SUBJECT, 1, [EO]);
  });

  it("defaults to 30 days and scopes a member to their groups", async () => {
    await callHistory(
      authed(["member"]),
      `subject_kind=association&subject_id=${SUBJECT}`,
    );
    expect(listDailyStats).toHaveBeenCalledWith("association", SUBJECT, 30, [EO]);
  });

  it("passes null for an admin", async () => {
    await callHistory(
      authed(["admin"], []),
      `subject_kind=process&subject_id=${SUBJECT}`,
    );
    expect(listDailyStats).toHaveBeenCalledWith("process", SUBJECT, 30, null);
  });

  it("404s a subject the caller cannot see — existence stays scoped", async () => {
    vi.mocked(listDailyStats).mockResolvedValue(null);
    const res = await callHistory(
      authed(["member"]),
      `subject_kind=process&subject_id=${SUBJECT}`,
    );
    expect(res.status).toBe(404);
  });
});

describe("GET /api/processes/[id]/runs/[runId]/log", () => {
  it("404s a run outside the caller's groups", async () => {
    vi.mocked(getProcess).mockResolvedValue({
      id: PROCESS_ID,
      group_id: "other",
    } as never);
    expect((await callLog(authed(["member"]))).status).toBe(404);
  });

  it("404s a run with no stored log rather than redirecting nowhere", async () => {
    vi.mocked(getRun).mockResolvedValue({ id: RUN_ID, log_ref: null } as never);
    const res = await callLog(authed(["member"]));
    expect(res.status).toBe(404);
    expect(presignGetUrl).not.toHaveBeenCalled();
  });

  it("302s to a presigned URL and forbids caching it", async () => {
    // The app never streams the bytes: a run log can be 10 MB of untrusted
    // output, and a shared cache must never hand one caller's signed URL to
    // another.
    vi.mocked(getRun).mockResolvedValue({
      id: RUN_ID,
      log_ref: "logs/runs/p/r.log",
    } as never);
    const res = await callLog(authed(["member"]));
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe("https://storage.example/signed");
    expect(res.headers.get("Cache-Control")).toContain("no-store");
    expect(presignGetUrl).toHaveBeenCalledWith("logs/runs/p/r.log");
  });

  it("404s a malformed run id", async () => {
    const res = await callLog(authed(["member"]), "not-a-uuid");
    expect(res.status).toBe(404);
    expect(getRun).not.toHaveBeenCalled();
  });
});
