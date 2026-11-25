// @vitest-environment node
/**
 * K-3: run rows carry `phase` / `phase_detail` (process-compute spec §5.2),
 * and the app's `ProcessRunPhase` is exactly the fixture's vocabulary.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { toApiRun, type ProcessRunPhase } from "@/lib/processes/storage";

const PHASES: ProcessRunPhase[] = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL("../../../tests/contract-fixtures/process-run-phase.json", import.meta.url),
    ),
    "utf8",
  ),
).phases;

const row = {
  id: "r1",
  process_id: "p1",
  revision_id: "v1",
  source_id: null,
  status: "queued" as const,
  attempts: 0,
  input_items: [],
  output_items: [],
  log_ref: null,
  error: null,
  rate_deferred_until: null,
  is_test: false,
  created_at: "2026-09-15T00:00:00Z",
  started_at: null,
  finished_at: null,
  phase: null,
  phase_detail: null,
  cancel_requested_at: null,
};

describe("run rows carry the K-3 phase", () => {
  it("the type admits exactly the fixture's phases", () => {
    // A compile-time list: adding a phase to the type without the fixture
    // (or the reverse) fails here.
    const typed: Record<ProcessRunPhase, true> = {
      pending_capacity: true,
      starting: true,
      running: true,
    };
    expect(Object.keys(typed)).toEqual(PHASES);
  });

  it("maps every fixture phase and its detail through", () => {
    for (const phase of PHASES) {
      const api = toApiRun({ ...row, phase, phase_detail: `detail for ${phase}` });
      expect(api.phase).toBe(phase);
      expect(api.phase_detail).toBe(`detail for ${phase}`);
    }
  });

  it("is null on a row without one", () => {
    expect(toApiRun(row).phase).toBeNull();
    expect(toApiRun(row).phase_detail).toBeNull();
  });
});

describe("run rows carry the K-4 cancel request", () => {
  it("passes cancel_requested_at through as ISO, or null", () => {
    expect(toApiRun(row).cancel_requested_at).toBeNull();
    const at = new Date("2026-11-25T12:00:00Z");
    expect(toApiRun({ ...row, cancel_requested_at: at }).cancel_requested_at).toBe(
      "2026-11-25T12:00:00.000Z",
    );
  });
});
