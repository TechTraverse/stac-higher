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
import { rerunRun, RunAlreadyQueuedError } from "@/lib/processes/storage";

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
