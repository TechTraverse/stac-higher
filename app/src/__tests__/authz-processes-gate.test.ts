// @vitest-environment node
/**
 * M5-A: process mutations are gated (operator+) and audited. The group
 * dimension is enforced in-route — the guard cannot see the DB — so these
 * pins are about WHICH routes are gated and what each mutation is recorded
 * as.
 */
import { describe, it, expect, vi } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";
import { applyApiGuard, type GuardContext } from "@/lib/authz/guard";
import { writeAudit } from "@/lib/audit/log";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

const P = "3a9f1c2e-0000-4000-8000-0000000000a1";
const CHILD = "3a9f1c2e-0000-4000-8000-0000000000b1";

describe("matchGatedRoute — /api/processes (M5-A)", () => {
  it("gates create, update and delete of a process", () => {
    expect(matchGatedRoute("POST", "/api/processes")).toEqual({
      action: "create",
      resourceType: "process",
      resourceId: null,
    });
    expect(matchGatedRoute("PUT", `/api/processes/${P}`)).toEqual({
      action: "update",
      resourceType: "process",
      resourceId: P,
    });
    expect(matchGatedRoute("DELETE", `/api/processes/${P}`)).toEqual({
      action: "delete",
      resourceType: "process",
      resourceId: P,
    });
  });

  it("records a deploy as its own action, against the PROCESS id", () => {
    // The revision does not exist yet at deploy time; the created revision id
    // reaches the audit row from the 201 body.
    expect(matchGatedRoute("POST", `/api/processes/${P}/revisions`)).toEqual({
      action: "deploy",
      resourceType: "process_revision",
      resourceId: P,
    });
  });

  it("gates a test-run request (ADR 0004 bridge)", () => {
    expect(matchGatedRoute("POST", `/api/processes/${P}/test`)).toEqual({
      action: "test",
      resourceType: "process",
      resourceId: P,
    });
  });

  it("gates source and output wiring, both create and remove", () => {
    expect(matchGatedRoute("POST", `/api/processes/${P}/sources`)).toEqual({
      action: "create",
      resourceType: "process_source",
      resourceId: P,
    });
    expect(
      matchGatedRoute("PUT", `/api/processes/${P}/sources/${CHILD}`),
    ).toEqual({
      action: "update",
      resourceType: "process_source",
      resourceId: CHILD,
    });
    expect(
      matchGatedRoute("DELETE", `/api/processes/${P}/sources/${CHILD}`),
    ).toEqual({
      action: "delete",
      resourceType: "process_source",
      resourceId: CHILD,
    });
    expect(matchGatedRoute("POST", `/api/processes/${P}/outputs`)).toEqual({
      action: "create",
      resourceType: "process_output",
      resourceId: P,
    });
    expect(
      matchGatedRoute("DELETE", `/api/processes/${P}/outputs/${CHILD}`),
    ).toEqual({
      action: "delete",
      resourceType: "process_output",
      resourceId: CHILD,
    });
  });

  it("gates the re-run verb as its own audited action", () => {
    expect(
      matchGatedRoute("POST", `/api/processes/${P}/runs/${CHILD}/rerun`),
    ).toEqual({ action: "rerun", resourceType: "process", resourceId: P });
    // K-4: cancel is gated and audited against the process, like rerun.
    expect(
      matchGatedRoute("POST", `/api/processes/${P}/runs/${CHILD}/cancel`),
    ).toEqual({ action: "cancel", resourceType: "process", resourceId: P });
  });

  it("leaves every read ungated (auth + group scoping in-route)", () => {
    expect(matchGatedRoute("GET", "/api/processes")).toBeNull();
    expect(matchGatedRoute("GET", `/api/processes/${P}`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/processes/${P}/revisions`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/processes/${P}/sources`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/processes/${P}/outputs`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/processes/${P}/runs`)).toBeNull();
    // The test-run poll is a read, like the connection-check poll.
    expect(
      matchGatedRoute("GET", `/api/processes/${P}/checks/${CHILD}`),
    ).toBeNull();
  });
});

// K-4 review A10: the guard itself refuses a member on /cancel and audits
// the allowed verb as `cancel` on the process.
vi.mock("@/lib/audit/log", () => ({ writeAudit: vi.fn(async () => true) }));

describe("the guard on POST …/runs/[runId]/cancel (K-4)", () => {
  const PROC = "11111111-1111-4111-8111-111111111111";
  const RUN = "22222222-2222-4222-8222-222222222222";
  const path = `/api/processes/${PROC}/runs/${RUN}/cancel`;

  function context(roles: CanonicalRole[]): GuardContext {
    const url = new URL(`http://localhost:4321${path}`);
    const auth: AuthContext = {
      authenticated: true,
      mode: "oidc",
      identity: { sub: "u1", email: "u@example.com", name: "U", groups: ["g"], roles },
    };
    return { request: new Request(url, { method: "POST" }), url, locals: { auth } };
  }

  const next = () =>
    vi.fn(async () => new Response(JSON.stringify({ outcome: "cancelled" }), { status: 202 }));

  it("refuses a member with 403 and never runs the handler", async () => {
    const handler = next();
    const res = await applyApiGuard(context(["member"]), handler);
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(handler).not.toHaveBeenCalled();
  });

  it("lets an operator through and audits `cancel` on the process", async () => {
    vi.mocked(writeAudit).mockClear();
    const handler = next();
    const res = await applyApiGuard(context(["operator"]), handler);
    expect(res.status).toBe(202);
    expect(handler).toHaveBeenCalledTimes(1);
    expect(writeAudit).toHaveBeenCalledWith(
      expect.objectContaining({ action: "cancel", resourceType: "process", resourceId: PROC }),
    );
  });
});
