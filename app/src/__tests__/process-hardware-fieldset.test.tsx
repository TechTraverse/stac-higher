import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { HardwareFieldset } from "@/components/processes/HardwareFieldset";
import type { PublicHardwareProfile } from "@/lib/processes/hardware-public";

/**
 * The deploy form's Hardware fieldset (K-2, process-compute spec §9): a
 * profile select grouped by tier, the profile's bounds on the inputs, a GPU
 * count only with an accelerator, and a one-line summary.
 */

const STANDARD: PublicHardwareProfile = {
  id: "standard", label: "Standard", description: "General purpose.", tier: "cpu",
  accelerator: null, cpu: { min: 0.25, max: 4, default: 1 },
  memory_mb: { min: 128, max: 16384, default: 512 }, gpu_count: null,
  max_queue_wait_seconds: 1800, image: null,
};
const LARGE: PublicHardwareProfile = {
  id: "cpu-large", label: "CPU — large", description: "Many cores.", tier: "cpu-large",
  accelerator: null, cpu: { min: 2, max: 8, default: 4 },
  memory_mb: { min: 4096, max: 32768, default: 8192 }, gpu_count: null,
  max_queue_wait_seconds: 3600, image: null,
};
const GPU_L4: PublicHardwareProfile = {
  id: "gpu-l4", label: "GPU — NVIDIA L4 (24 GB)", description: "Single L4.", tier: "gpu",
  accelerator: { vendor: "nvidia", model: "l4", memory_gb: 24 },
  cpu: { min: 2, max: 16, default: 4 }, memory_mb: { min: 4096, max: 65536, default: 16384 },
  gpu_count: { min: 1, max: 1, default: 1 }, max_queue_wait_seconds: 7200,
  image: "process-runtime-cuda",
};
const PROFILES = [GPU_L4, STANDARD, LARGE]; // deliberately out of tier order

function setup(overrides: Partial<React.ComponentProps<typeof HardwareFieldset>> = {}) {
  const onChange = vi.fn();
  render(
    <HardwareFieldset
      profiles={PROFILES}
      hardware={{ profile: "standard", cpu: 1, gpu_count: 0 }}
      memoryMb={512}
      onChange={onChange}
      {...overrides}
    />,
  );
  return { onChange };
}

describe("HardwareFieldset", () => {
  it("is a group named Hardware", () => {
    setup();
    expect(screen.getByRole("group", { name: "Hardware" })).toBeInTheDocument();
  });

  it("groups profiles by tier in spec order and selects the current one", () => {
    setup();
    const select = screen.getByLabelText("Hardware profile") as HTMLSelectElement;
    expect(select.value).toBe("standard");
    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => ({
      label: g.label,
      options: Array.from(g.querySelectorAll("option")).map((o) => o.value),
    }));
    expect(groups).toEqual([
      { label: "CPU", options: ["standard"] },
      { label: "Large CPU", options: ["cpu-large"] },
      { label: "GPU", options: ["gpu-l4"] },
    ]);
  });

  it("shows the description, bounds the cpu and memory inputs, hides GPU count without an accelerator", () => {
    setup();
    expect(screen.getByText("General purpose.")).toBeInTheDocument();
    const cpu = screen.getByLabelText("CPU (cores)") as HTMLInputElement;
    expect(cpu.min).toBe("0.25");
    expect(cpu.max).toBe("4");
    expect(cpu.step).toBe("0.25");
    const mem = screen.getByLabelText("Memory (MB)") as HTMLInputElement;
    expect(mem.min).toBe("128");
    expect(mem.max).toBe("16384");
    expect(screen.queryByLabelText("GPU count")).toBeNull();
    expect(screen.getByTestId("hardware-summary")).toHaveTextContent(
      "1 vCPU · 512 MB · waits up to 30 min",
    );
  });

  it("resets cpu, memory and gpu_count to the new profile's defaults on a profile change", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByLabelText("Hardware profile"), { target: { value: "gpu-l4" } });
    expect(onChange).toHaveBeenCalledWith({
      hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 },
      memoryMb: 16384,
    });
  });

  it("shows the accelerator badge and a bounded GPU count for a GPU profile", () => {
    setup({ hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 }, memoryMb: 16384 });
    expect(screen.getByText("NVIDIA L4 · 24 GB")).toBeInTheDocument();
    const gpus = screen.getByLabelText("GPU count") as HTMLInputElement;
    expect(gpus.min).toBe("1");
    expect(gpus.max).toBe("1");
    expect(screen.getByTestId("hardware-summary")).toHaveTextContent(
      "4 vCPU · 16 GB · 1× L4 · waits up to 2 h",
    );
  });

  it("reports cpu and memory edits without touching the rest", () => {
    const { onChange } = setup();
    fireEvent.change(screen.getByLabelText("CPU (cores)"), { target: { value: "2" } });
    expect(onChange).toHaveBeenLastCalledWith({
      hardware: { profile: "standard", cpu: 2, gpu_count: 0 },
      memoryMb: 512,
    });
    fireEvent.change(screen.getByLabelText("Memory (MB)"), { target: { value: "1024" } });
    expect(onChange).toHaveBeenLastCalledWith({
      hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
      memoryMb: 1024,
    });
  });

  it("reports a GPU count edit without touching the rest", () => {
    const { onChange } = setup({
      profiles: [STANDARD, { ...GPU_L4, gpu_count: { min: 1, max: 4, default: 1 } }],
      hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 },
      memoryMb: 16384,
    });
    fireEvent.change(screen.getByLabelText("GPU count"), { target: { value: "2" } });
    expect(onChange).toHaveBeenLastCalledWith({
      hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 2 },
      memoryMb: 16384,
    });
  });

  it("shows an out-of-bounds value inline in the write gate's words and marks that input", () => {
    setup({ memoryMb: 32768 });
    expect(screen.getByTestId("hardware-issue")).toHaveTextContent(
      "memory_mb 32768 is outside profile 'standard' bounds 128–16384",
    );
    const mem = screen.getByLabelText("Memory (MB)");
    expect(mem).toHaveAttribute("aria-invalid", "true");
    expect(mem).toHaveAccessibleDescription(/memory_mb 32768 is outside/);
    expect(screen.getByLabelText("CPU (cores)")).not.toHaveAttribute("aria-invalid");
  });

  it("flags a cleared input and fractional memory", () => {
    const { unmount } = render(
      <HardwareFieldset
        profiles={PROFILES}
        hardware={{ profile: "standard", cpu: 0, gpu_count: 0 }}
        memoryMb={512}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText("CPU (cores)")).toHaveAttribute("aria-invalid", "true");
    unmount();
    setup({ memoryMb: 512.5 });
    expect(screen.getByTestId("hardware-issue")).toHaveTextContent("Memory must be a whole number of MB");
    expect(screen.getByLabelText("Memory (MB)")).toHaveAttribute("aria-invalid", "true");
  });

  it("shows no issue inside the bounds", () => {
    setup();
    expect(screen.queryByTestId("hardware-issue")).toBeNull();
  });

  it("describes the select with the profile description and announces the summary politely", () => {
    setup({ hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 }, memoryMb: 16384 });
    expect(screen.getByLabelText("Hardware profile")).toHaveAccessibleDescription(
      /Single L4\.\s*NVIDIA L4 · 24 GB/,
    );
    expect(screen.getByTestId("hardware-summary")).toHaveAttribute("aria-live", "polite");
  });

  it("says so when the profiles failed to load, rather than loading forever", () => {
    setup({ profiles: undefined, profilesError: "Request failed: 500" });
    expect(screen.queryByText(/Loading hardware profiles/)).toBeNull();
    expect(screen.getByText(/Could not load the hardware profiles \(Request failed: 500\)/)).toBeInTheDocument();
    expect(screen.getByLabelText("Hardware profile")).toBeDisabled();
    // Nothing to check against: no inline issue, and the summary claims nothing.
    expect(screen.queryByTestId("hardware-issue")).toBeNull();
    expect(screen.getByTestId("hardware-summary")).toHaveTextContent(/^1 vCPU · 512 MB$/);
  });

  it("keeps a profile this deployment no longer has as a disabled option and says so", () => {
    setup({ hardware: { profile: "gone", cpu: 1, gpu_count: 0 } });
    const select = screen.getByLabelText("Hardware profile") as HTMLSelectElement;
    expect(select.value).toBe("gone");
    const gone = Array.from(select.options).find((o) => o.value === "gone");
    expect(gone?.disabled).toBe(true);
    expect(screen.getByTestId("hardware-summary")).toHaveTextContent("gone (not in this deployment)");
  });

  it("renders a loading placeholder while the profiles are undefined, without calling the profile missing", () => {
    setup({ profiles: undefined });
    expect(screen.getByLabelText("Hardware profile")).toBeDisabled();
    expect(screen.getByText(/Loading hardware profiles/)).toBeInTheDocument();
    expect(screen.queryByText(/not in this deployment/)).toBeNull();
    expect(screen.getByTestId("hardware-summary")).toHaveTextContent("1 vCPU · 512 MB");
  });

  it("disables every control when disabled", () => {
    setup({ disabled: true, hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 }, memoryMb: 16384 });
    expect(screen.getByLabelText("Hardware profile")).toBeDisabled();
    expect(screen.getByLabelText("CPU (cores)")).toBeDisabled();
    expect(screen.getByLabelText("Memory (MB)")).toBeDisabled();
    expect(screen.getByLabelText("GPU count")).toBeDisabled();
  });
});
