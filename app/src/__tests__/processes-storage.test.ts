// @vitest-environment node
// (server-side db code — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import { query } from "@/lib/db/connection";
import { cancelRun, rerunRun, RunAlreadyQueuedError } from "@/lib/processes/storage";

const mockQuery = vi.mocked(query);

const PROCESS_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";
const RUN_ID = "3a9f1c2e-0000-4000-8000-0000000000d1";

beforeEach(() => {
  mockQuery.mockReset();
});

describe("rerunRun", () => {
  it("maps a unique-violation (23505) to RunAlreadyQueuedError, not the raw DB text", async () => {
    // The dead row flipping to `queued` collides with the source's existing
    // queued run on process_runs_queued_source_idx (#74).
    mockQuery.mockRejectedValueOnce({
      code: "23505",
      message:
        'duplicate key value violates unique constraint "process_runs_queued_source_idx"',
    });
    const err = await rerunRun(PROCESS_ID, RUN_ID).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RunAlreadyQueuedError);
    expect((err as Error).message).toContain("already queued");
    expect((err as Error).message).not.toContain("duplicate key");
  });

  it("rethrows any other database error unchanged", async () => {
    const boom = Object.assign(new Error("connection reset"), { code: "08006" });
    mockQuery.mockRejectedValueOnce(boom);
    await expect(rerunRun(PROCESS_ID, RUN_ID)).rejects.toBe(boom);
  });
});

describe("rerunRun (K-4)", () => {
  it("accepts dead or cancelled rows and clears every executor column", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] } as never);
    await rerunRun(PROCESS_ID, RUN_ID);
    const sql = String(mockQuery.mock.calls[0][0]);
    expect(sql).toContain("status IN ('dead','cancelled')");
    for (const column of [
      "executor_backend = NULL",
      "executor_handle = NULL",
      "submitted_at = NULL",
      "cancel_requested_at = NULL",
      "credentials_expire_at = NULL",
    ]) {
      expect(sql).toContain(column);
    }
  });
});

describe("cancelRun (K-4)", () => {
  const row = (prior: string) => ({
    id: RUN_ID, process_id: PROCESS_ID, revision_id: "r", source_id: null,
    status: prior === "running" ? "running" : "cancelled", attempts: 1,
    input_items: [], output_items: [], log_ref: null, error: null,
    rate_deferred_until: null, is_test: false, created_at: "2026-11-25T00:00:00Z",
    started_at: null, finished_at: null, phase: null, phase_detail: null,
    cancel_requested_at: "2026-11-25T00:01:00Z", prior_status: prior,
  });

  it("is ONE statement: locks the row, flags it and NOTIFYs the pipeline", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [row("running")] } as never);
    await cancelRun(PROCESS_ID, RUN_ID);
    expect(mockQuery).toHaveBeenCalledTimes(1);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status IN ('queued','failed','running')");
    expect(sql).toContain("FOR UPDATE");
    expect(sql).toContain("coalesce(r.cancel_requested_at, now())");
    expect(sql).toContain("pg_notify('process_run_cancel', '')");
    expect(params).toEqual([RUN_ID, PROCESS_ID]);
  });

  it("maps the locked row's prior status to the outcome", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [row("running")] } as never);
    expect((await cancelRun(PROCESS_ID, RUN_ID))?.outcome).toBe("cancel_requested");
    // A retry-pending `failed` run (R4) flips at once too.
    mockQuery.mockResolvedValueOnce({ rows: [row("failed")] } as never);
    expect((await cancelRun(PROCESS_ID, RUN_ID))?.outcome).toBe("cancelled");
    mockQuery.mockResolvedValueOnce({ rows: [row("queued")] } as never);
    const queued = await cancelRun(PROCESS_ID, RUN_ID);
    expect(queued?.outcome).toBe("cancelled");
    expect(queued?.run.cancel_requested_at).toBe("2026-11-25T00:01:00Z");
  });

  it("returns null when nothing was cancellable", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] } as never);
    expect(await cancelRun(PROCESS_ID, RUN_ID)).toBeNull();
  });
});
