import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/**
 * The deploy card's `Network access` control (GOES spec §4, ADR 0018).
 *
 * Slice 1 ships the FIELD, not the levels: every process runs isolated and
 * inputs are staged into the run, so the control shows the four levels with
 * only `isolated` selectable and says why. The deploy payload must carry the
 * block explicitly — the write gate would default it, but a revision should
 * record the operator's choice, not a silent default.
 */

const mutateAsync = vi.fn(async (_args: unknown) => ({}));

vi.mock("@/lib/processes/queries", () => ({
  useDeployRevision: () => ({ mutateAsync, isPending: false }),
}));
vi.mock("@/lib/connections/queries", () => ({
  useConnections: () => ({ data: [] }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { CodeCard } from "@/components/processes/ProcessDetailPage";

const PROCESS_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";

function setup(kind: "transform" | "extractor" = "transform") {
  render(
    <CodeCard
      id={PROCESS_ID}
      groupId="earth-observation"
      currentCode="print(1)"
      currentEnv={[]}
      currentRevision={null}
      canMutate
      kind={kind}
    />,
  );
}

beforeEach(() => {
  mutateAsync.mockClear();
});

describe("CodeCard network access", () => {
  it("offers the four levels with only isolated selectable this slice", () => {
    setup();
    const select = screen.getByLabelText("Network access") as HTMLSelectElement;
    expect(select).toHaveValue("isolated");
    const options = Array.from(select.options).map((o) => ({
      label: o.textContent,
      disabled: o.disabled,
    }));
    expect(options).toEqual([
      { label: "Isolated (platform storage only)", disabled: false },
      { label: "Inputs (hosts of the input assets)", disabled: true },
      { label: "Named hosts", disabled: true },
      { label: "Open internet", disabled: true },
    ]);
    expect(screen.getByText(/Higher levels arrive with the egress proxy/)).toBeInTheDocument();
    expect(screen.getByText(/PROCESS_NETWORK_MAX/)).toBeInTheDocument();
  });

  it("deploys with an explicit isolated network block", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const { input } = mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: { network: unknown } };
    };
    expect(input.runtime.network).toEqual({ level: "isolated", hosts: [] });
  });
});

describe("CodeCard timeout default (G-6, GOES spec §6.5)", () => {
  it("defaults an extractor's timeout to 120 s and a transform's to 900 s", async () => {
    setup("extractor");
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const extractorInput = mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: { timeout_seconds: number } };
    };
    expect(extractorInput.input.runtime.timeout_seconds).toBe(120);

    mutateAsync.mockClear();
    setup("transform");
    fireEvent.click(screen.getAllByRole("button", { name: /Deploy revision/ })[1]);
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const transformInput = mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: { timeout_seconds: number } };
    };
    expect(transformInput.input.runtime.timeout_seconds).toBe(900);
  });
});
