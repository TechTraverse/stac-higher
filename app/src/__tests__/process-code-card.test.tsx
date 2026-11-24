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

const PROFILES = [
  {
    id: "standard", label: "Standard", description: "General purpose.", tier: "cpu",
    accelerator: null, cpu: { min: 0.25, max: 4, default: 1 },
    memory_mb: { min: 128, max: 16384, default: 512 }, gpu_count: null,
    max_queue_wait_seconds: 1800, image: null,
  },
  {
    id: "cpu-large", label: "CPU — large", description: "Many cores.", tier: "cpu-large",
    accelerator: null, cpu: { min: 2, max: 8, default: 4 },
    memory_mb: { min: 4096, max: 32768, default: 8192 }, gpu_count: null,
    max_queue_wait_seconds: 3600, image: null,
  },
];

vi.mock("@/lib/processes/queries", () => ({
  useDeployRevision: () => ({ mutateAsync, isPending: false }),
  useHardwareProfiles: () => ({ data: { backend: "docker", profiles: PROFILES } }),
}));
vi.mock("@/lib/connections/queries", () => ({
  useConnections: () => ({ data: [] }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const { imagesState } = vi.hoisted(() => ({ imagesState: { images: [] as unknown[] } }));
vi.mock("@/lib/images/queries", () => ({
  useImages: () => ({ data: { images: imagesState.images, scan_window_days: 30 } }),
  useAddImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useImageScan: () => ({ data: undefined }),
  useImagePolicy: () => ({ data: undefined }),
}));

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

describe("CodeCard runtime chooser (C-3, container-images spec §9.3)", () => {
  const DIGEST = "sha256:" + "a".repeat(64);
  const APPROVED = {
    id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
    reference: "ghcr.io/org/satpy-runtime",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    stale: false,
    registry_connection: null,
  };
  const FLAGGED = { ...APPROVED, id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e71", reference: "ghcr.io/org/old", status: "flagged" };

  beforeEach(() => {
    imagesState.images = [APPROVED, FLAGGED];
  });

  function deployedInput() {
    return mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: Record<string, unknown>; code: string | null };
    };
  }

  it("offers the three runtimes with the platform image first and its alias select", () => {
    setup();
    expect(screen.getByRole("group", { name: "Runtime" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Platform image/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: /Custom image \+ your code/ })).not.toBeChecked();
    expect(screen.getByRole("radio", { name: /Container image/ })).not.toBeChecked();
    const alias = screen.getByLabelText("Image variant") as HTMLSelectElement;
    expect(Array.from(alias.options).map((o) => o.value)).toEqual(["default", "stactools"]);
  });

  it("deploys inline code on the chosen platform alias", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Image variant"), { target: { value: "stactools" } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedInput().input.runtime).toMatchObject({
      kind: "inline_python",
      image: null,
      runtime_image: "stactools",
    });
    expect(deployedInput().input.code).toBe("print(1)");
  });

  it("needs an approved image for your code on your image, and lists the flagged one disabled", async () => {
    setup();
    fireEvent.click(screen.getByRole("radio", { name: /Custom image \+ your code/ }));
    expect(screen.getByRole("button", { name: /Deploy revision/ })).toBeDisabled();
    expect(screen.getByText("Choose an approved image", { selector: "span" })).toBeInTheDocument();
    const picker = screen.getByLabelText("Image") as HTMLSelectElement;
    const flagged = Array.from(picker.options).find((o) => o.value === FLAGGED.id);
    expect(flagged?.disabled).toBe(true);
    expect(flagged?.textContent).toMatch(/\(Flagged\)$/);

    fireEvent.change(picker, { target: { value: APPROVED.id } });
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedInput().input.runtime).toMatchObject({
      kind: "inline_python_on_image",
      image: { id: APPROVED.id, reference: APPROVED.reference, digest: DIGEST },
      runtime_image: null,
    });
    expect(deployedInput().input.code).toBe("print(1)");
  });

  it("hides the editor for a container image and deploys its command with no code", async () => {
    setup();
    fireEvent.click(screen.getByRole("radio", { name: /Container image/ }));
    expect(screen.queryByLabelText("Process code")).toBeNull();
    fireEvent.change(screen.getByLabelText("Image"), { target: { value: APPROVED.id } });
    fireEvent.change(screen.getByLabelText("Command (optional)"), {
      target: { value: "tool --run 'a b'" },
    });
    expect(screen.getByTestId("command-preview")).toHaveTextContent('["tool","--run","a b"]');
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedInput().input.runtime).toMatchObject({
      kind: "container",
      command: ["tool", "--run", "a b"],
    });
    expect(deployedInput().input.code).toBeNull();
  });

  it("explains a broken command and will not deploy it", () => {
    setup();
    fireEvent.click(screen.getByRole("radio", { name: /Container image/ }));
    fireEvent.change(screen.getByLabelText("Image"), { target: { value: APPROVED.id } });
    fireEvent.change(screen.getByLabelText("Command (optional)"), { target: { value: "tool 'x" } });
    expect(screen.getByTestId("command-preview")).toHaveTextContent(/Unterminated single quote/);
    expect(screen.getByRole("button", { name: /Deploy revision/ })).toBeDisabled();
  });

  it("starts from the current revision's kind, image and command", () => {
    render(
      <CodeCard
        id={PROCESS_ID}
        groupId="earth-observation"
        currentCode={null}
        currentEnv={[]}
        currentRevision="3a9f1c2e-0000-4000-8000-0000000000b1"
        currentRuntime={{
          kind: "container",
          image: { id: APPROVED.id, reference: APPROVED.reference, digest: DIGEST },
          command: ["tool", "a b"],
        }}
        canMutate
        kind="transform"
      />,
    );
    expect(screen.getByRole("radio", { name: /Container image/ })).toBeChecked();
    expect((screen.getByLabelText("Image") as HTMLSelectElement).value).toBe(APPROVED.id);
    expect(screen.getByLabelText("Command (optional)")).toHaveValue("tool 'a b'");
  });
});

describe("CodeCard hardware (K-2, process-compute spec §9)", () => {
  const DEPLOYED = {
    kind: "inline_python",
    image: null,
    runtime_image: "default",
    memory_mb: 8192,
    timeout_seconds: 3600,
    retry: { max_attempts: 3, backoff: "exponential" },
    network: { level: "isolated", hosts: [] },
    hardware: { profile: "cpu-large", cpu: 4, gpu_count: 0 },
  };

  function card(currentRevision: string | null, currentRuntime: Record<string, unknown> | null) {
    return (
      <CodeCard
        id={PROCESS_ID}
        groupId="earth-observation"
        currentCode="print(1)"
        currentEnv={[]}
        currentRevision={currentRevision}
        currentRuntime={currentRuntime}
        canMutate
        kind="transform"
      />
    );
  }

  function deployedRuntime() {
    return (
      mutateAsync.mock.calls[0][0] as unknown as {
        input: { runtime: { hardware: unknown; memory_mb: number; timeout_seconds: number } };
      }
    ).input.runtime;
  }

  it("deploys the schema defaults when there is no revision", async () => {
    setup();
    expect(screen.getByRole("group", { name: "Hardware" })).toBeInTheDocument();
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("standard");
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(512);
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedRuntime().hardware).toEqual({ profile: "standard", cpu: 1, gpu_count: 0 });
    expect(deployedRuntime().memory_mb).toBe(512);
    expect(deployedRuntime().timeout_seconds).toBe(900);
  });

  it("starts from the current revision's hardware, memory and timeout", () => {
    render(card("rev-1", DEPLOYED));
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("cpu-large");
    expect(screen.getByLabelText("CPU (cores)")).toHaveValue(4);
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
    expect(screen.getByLabelText("Timeout (seconds)")).toHaveValue(3600);
  });

  it("re-syncs when the current revision moves", () => {
    const view = render(card(null, null));
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(512);
    view.rerender(card("rev-2", DEPLOYED));
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("cpu-large");
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
    expect(screen.getByLabelText("Timeout (seconds)")).toHaveValue(3600);
  });

  it("re-syncs when the revision's runtime arrives after the revision id", () => {
    const view = render(card("rev-1", null));
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("standard");
    view.rerender(card("rev-1", DEPLOYED));
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("cpu-large");
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
  });

  it("deploys the picked profile at its defaults after a profile change", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Hardware profile"), { target: { value: "cpu-large" } });
    expect(screen.getByLabelText("CPU (cores)")).toHaveValue(4);
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    expect(deployedRuntime().hardware).toEqual({ profile: "cpu-large", cpu: 4, gpu_count: 0 });
    expect(deployedRuntime().memory_mb).toBe(8192);
  });
});
