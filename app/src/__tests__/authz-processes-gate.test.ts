// @vitest-environment node
/**
 * M5-A: process mutations are gated (operator+) and audited. The group
 * dimension is enforced in-route — the guard cannot see the DB — so these
 * pins are about WHICH routes are gated and what each mutation is recorded
 * as.
 */
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

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
