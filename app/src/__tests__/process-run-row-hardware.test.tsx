import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

/**
 * Run rows show the hardware their pinned revision asked for (K-2,
 * process-compute spec §9): runs and revisions are both already on the page,
 * so the row joins them rather than the run carrying a copy.
 */

const { state } = vi.hoisted(() => ({ state: { profilesLoaded: true } }));

vi.mock("@/lib/processes/queries", () => ({
  useRuns: () => ({ data: RUNS, isLoading: false }),
  useRerunRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useHardwareProfiles: () => ({
    data: state.profilesLoaded ? { backend: "docker", profiles: PROFILES } : undefined,
  }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { RunsCard } from "@/components/processes/ProcessDetailPage";
import type { ProcessRevision } from "@/lib/processes/types";

const PROFILES = [
  {
    id: "standard", label: "Standard", description: "General purpose.", tier: "cpu",
    accelerator: null, cpu: { min: 0.25, max: 4, default: 1 },
    memory_mb: { min: 128, max: 16384, default: 512 }, gpu_count: null,
    max_queue_wait_seconds: 1800, image: null,
  },
];

const REVISIONS: ProcessRevision[] = [
  {
    id: "rev-old", process_id: "p1", code: null, env: [], created_by: "x", created_at: "2026-09-01T00:00:00Z",
    // Predates K-1: no hardware block — it reads as `standard`.
    runtime: { kind: "inline_python", image: null, memory_mb: 512, timeout_seconds: 900 },
  },
  {
    id: "rev-new", process_id: "p1", code: null, env: [], created_by: "x", created_at: "2026-09-02T00:00:00Z",
    runtime: {
      kind: "inline_python", image: null, memory_mb: 2048, timeout_seconds: 900,
      hardware: { profile: "standard", cpu: 2, gpu_count: 0 },
    },
  },
  {
    id: "rev-gone", process_id: "p1", code: null, env: [], created_by: "x", created_at: "2026-09-03T00:00:00Z",
    runtime: {
      kind: "inline_python", image: null, memory_mb: 4096, timeout_seconds: 900,
      hardware: { profile: "retired", cpu: 1, gpu_count: 0 },
    },
  },
];

const baseRun = {
  process_id: "p1", source_id: null, status: "succeeded" as const, attempts: 1,
  input_items: [], output_items: [], log_ref: null, error: null, rate_deferred_until: null,
  is_test: false, created_at: "2026-09-02T01:00:00Z", started_at: null, finished_at: null,
};
const RUNS = [
  { ...baseRun, id: "run-1", revision_id: "rev-new" },
  { ...baseRun, id: "run-2", revision_id: "rev-old" },
  { ...baseRun, id: "run-3", revision_id: "rev-gone" },
  { ...baseRun, id: "run-4", revision_id: "rev-missing" },
];

function hardwareCells() {
  return screen.getAllByTestId("run-hardware").map((el) => el.textContent);
}

describe("RunRow pinned hardware (K-2, spec §9)", () => {
  it("summarises each run's pinned revision and says nothing for an unknown revision", () => {
    state.profilesLoaded = true;
    render(<RunsCard id="p1" canMutate={false} isExtractor={false} revisions={REVISIONS} />);
    expect(hardwareCells()).toEqual([
      "2 vCPU · 2 GB · waits up to 30 min",
      "1 vCPU · 512 MB · waits up to 30 min",
      "retired (not in this deployment) · 1 vCPU · 4 GB",
    ]);
  });

  it("shows the bare counts while the profiles load", () => {
    state.profilesLoaded = false;
    render(<RunsCard id="p1" canMutate={false} isExtractor={false} revisions={REVISIONS} />);
    expect(hardwareCells()).toEqual(["2 vCPU · 2 GB", "1 vCPU · 512 MB", "1 vCPU · 4 GB"]);
  });
});
