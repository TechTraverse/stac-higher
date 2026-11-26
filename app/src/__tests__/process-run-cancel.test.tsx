import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * K-4 (process-compute spec §9): the run row's Cancel verb, the `cancelled`
 * badge, the *Cancelling…* chip, and *Waiting for capacity* on a submitted run.
 */

const { state, toastSpy } = vi.hoisted(() => ({
  state: {
    runs: [] as unknown[],
    cancel: vi.fn(),
  },
  toastSpy: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/lib/processes/queries", () => ({
  useRuns: () => ({ data: state.runs, isLoading: false }),
  useRerunRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCancelRun: () => ({ mutateAsync: state.cancel, isPending: false }),
  useHardwareProfiles: () => ({ data: undefined }),
}));
vi.mock("sonner", () => ({ toast: toastSpy }));

import { RunsCard } from "@/components/processes/ProcessDetailPage";

const baseRun = {
  process_id: "p1", source_id: null, revision_id: "rev-missing", attempts: 1,
  input_items: [], output_items: [], log_ref: null, error: null, rate_deferred_until: null,
  is_test: false, created_at: "2026-11-25T01:00:00Z", started_at: null, finished_at: null,
  phase: null, phase_detail: null, cancel_requested_at: null,
};

function runs(...statuses: string[]) {
  return statuses.map((status, i) => ({ ...baseRun, id: `run-${i}`, status }));
}

function renderCard({ canMutate = true, isExtractor = false } = {}) {
  return render(
    <RunsCard id="p1" canMutate={canMutate} isExtractor={isExtractor} revisions={[]} />,
  );
}

beforeEach(() => {
  state.cancel = vi.fn();
  toastSpy.success.mockReset();
  toastSpy.error.mockReset();
});

describe("RunRow cancel (K-4)", () => {
  it("shows Cancel on queued, retrying and running rows for an operator only", () => {
    state.runs = runs("queued", "failed", "running", "succeeded", "dead", "cancelled");
    const { unmount } = renderCard();
    expect(screen.getAllByTestId("run-cancel")).toHaveLength(3);
    unmount();
    renderCard({ canMutate: false });
    expect(screen.queryByTestId("run-cancel")).toBeNull();
  });

  it("asks before cancelling, then calls the verb once", async () => {
    state.runs = runs("running");
    state.cancel.mockResolvedValue({ run: {}, outcome: "cancel_requested" });
    renderCard();
    fireEvent.click(screen.getByTestId("run-cancel"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Cancel this run?")).toBeInTheDocument();
    expect(dialog).toHaveTextContent("its log is kept");
    expect(dialog).toHaveTextContent("can be re-run");
    // Fits a queued or retrying run too, not only a running one (review A12).
    expect(within(dialog).getByRole("button", { name: "Don't cancel" })).toBeInTheDocument();
    expect(state.cancel).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("run-cancel-confirm"));
    await waitFor(() => expect(state.cancel).toHaveBeenCalledTimes(1));
    expect(state.cancel).toHaveBeenCalledWith({ id: "p1", runId: "run-0" });
    await waitFor(() =>
      expect(toastSpy.success).toHaveBeenCalledWith("Cancel requested — stopping the run"),
    );
  });

  it("says Run cancelled when a queued run flipped at once", async () => {
    state.runs = runs("queued");
    state.cancel.mockResolvedValue({ run: {}, outcome: "cancelled" });
    renderCard();
    fireEvent.click(screen.getByTestId("run-cancel"));
    fireEvent.click(await screen.findByTestId("run-cancel-confirm"));
    await waitFor(() => expect(toastSpy.success).toHaveBeenCalledWith("Run cancelled"));
  });

  it("shows the server's message when the cancel is refused", async () => {
    state.runs = runs("queued");
    state.cancel.mockRejectedValue(new Error("this one is 'succeeded'"));
    renderCard();
    fireEvent.click(screen.getByTestId("run-cancel"));
    fireEvent.click(await screen.findByTestId("run-cancel-confirm"));
    await waitFor(() => expect(toastSpy.error).toHaveBeenCalledWith("this one is 'succeeded'"));
  });

  it("marks a running run whose cancel is pending", () => {
    state.runs = [
      { ...baseRun, id: "run-0", status: "running", phase: "starting",
        cancel_requested_at: "2026-11-25T01:01:00Z" },
    ];
    renderCard();
    expect(screen.getByTestId("run-phase-cancelling")).toHaveTextContent("Cancelling…");
    expect(screen.queryByTestId("run-cancel")).toBeNull();
    expect(screen.queryByTestId("run-phase-starting")).toBeNull();
  });

  it("shows the cancelled badge and offers Re-run, except to an extractor", () => {
    state.runs = runs("cancelled");
    const { unmount } = renderCard();
    expect(screen.getByTestId("run-status-cancelled")).toHaveTextContent("cancelled");
    expect(screen.getByRole("button", { name: /Re-run/ })).toBeInTheDocument();
    unmount();
    renderCard({ isExtractor: true });
    expect(screen.queryByRole("button", { name: /Re-run/ })).toBeNull();
  });

  it("lets an operator cancel an extractor run (plan R17)", () => {
    state.runs = runs("running");
    renderCard({ isExtractor: true });
    expect(screen.getByTestId("run-cancel")).toBeInTheDocument();
  });

  it("shows Waiting for capacity on a submitted run too", () => {
    state.runs = [
      { ...baseRun, id: "run-0", status: "running", phase: "pending_capacity",
        phase_detail: "Kueue: waiting for quota" },
    ];
    renderCard();
    const chip = screen.getByTestId("run-phase-pending_capacity");
    expect(chip).toHaveTextContent("Waiting for capacity");
    expect(chip).toHaveAttribute("title", "Kueue: waiting for quota");
  });
});
