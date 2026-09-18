# K-2 · Hardware Picker in the UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The deploy form on a process page lets an operator pick a hardware profile and set CPU / memory / GPU count inside that profile's bounds, the form starts from what the current revision actually deployed (hardware, memory, timeout — today only code and env are synced), and every run row names the hardware its pinned revision asked for.

**Architecture:** A client-safe module `app/src/lib/processes/hardware-public.ts` holds the public profile type (what `GET /api/processes/hardware-profiles` returns — K-1's `hardware.ts` imports `node:fs` and must never reach the browser bundle) plus the pure helpers the UI formats with. A `useHardwareProfiles()` TanStack hook (key `processKeys.hardwareProfiles()`) fetches the set once per page. A new app-only component `HardwareFieldset` (native `<select>` with `<optgroup>` per tier, bounded `<Input type="number">`s, a one-line summary) replaces `CodeCard`'s bare memory input; `CodeCard` gains a `currentRuntime` prop it syncs its hardware / memory / timeout state from whenever the current revision changes. `RunsCard` joins runs to revisions (both already loaded on the page) so each `RunRow` shows the pinned hardware summary. No server change: the write gate (K-1) still refuses out-of-bounds values with a 400 the form surfaces as a toast.

**Tech Stack:** Astro 7 + React 19 islands, TanStack Query, shadcn `Input`/`Label`/`Badge`, native `<select>`, vitest + @testing-library/react (jsdom), Zod (read-side runtime schema).

**Spec:** `docs/superpowers/specs/2026-09-02-process-compute-k8s-kueue-design.md` §9 (UI — the deploy-form and run-row bullets; the `phase` chip, Cancel button and `cancelled` badge are K-3/K-4's), §3.3 (what the app exposes), §4 (the `hardware` block and its bounds); ADR 0019; GitHub issue #10 "K-2 · Hardware picker in the UI"; K-1's landed note in the K epic #2 and issue #18 for the shipped interfaces.

## Global Constraints

- **Worktree:** `git worktree add .claude/worktrees/k2-hardware-picker -b feat/k2-hardware-picker main` (GitHub issue #10) — only after **K-1 has merged into `main`** (it has, 2026-09-15) (Task 0 checks). Then `npm install` at the worktree root.
- **Gates:** `npm run verify` from the worktree root before each commit (app-scoped typecheck + build + unit tests). Teammates never run e2e, the dev server, or Docker — the lead runs e2e (`processes` spec, then the whole suite) after merge.
- **Conventions (spec §9, the processes pages):** plain `useState` forms, native `<select>` with disabled options, TanStack mutations, server-side Zod. shadcn primitives from `@/components/ui/*` (app-only) or `@stac-higher/shared`; **never hand-edit `components/ui/`**. `lucide-react` icons. No new dependency.
- **The profile shape the UI sees (spec §3.3):** `GET /api/processes/hardware-profiles` → `{ "backend": "docker", "profiles": [ { id, label, description, tier ("cpu" | "cpu-large" | "gpu"), accelerator (null | {vendor, model, memory_gb}), cpu {min, max, default}, memory_mb {min, max, default}, gpu_count (null | {min, max, default}), max_queue_wait_seconds, image (null | string) } ] }` — no `backend` block on a profile. Exactly one profile has `id: "standard"`; bounds are inclusive.
- **The runtime block (spec §4), verbatim:** `"hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }`; `memory_mb` and `timeout_seconds` stay top-level. A revision stored without the block reads as `{profile: "standard", cpu: 1, gpu_count: 0}` (`processRuntimeReadSchema` applies that default — the form never has to).
- **Tier labels (spec §9):** `cpu` → `CPU`, `cpu-large` → `Large CPU`, `gpu` → `GPU`. Accelerator badge text: `${vendor} ${model} · ${memory_gb} GB` (e.g. `NVIDIA L4 · 24 GB`). Summary line: `4 vCPU · 16 GB · 1× L4 · waits up to 2 h` — the GPU segment only when `gpu_count > 0`.
- **Changing the profile resets the numbers to the profile's defaults** (`cpu.default`, `memory_mb.default`, `gpu_count?.default ?? 0`). The timeout is not a profile field and is not reset.
- **Sync on load (spec §9, fixed in passing):** the current revision's `hardware`, `memory_mb` and `timeout_seconds` are synced into the form whenever `currentRevision` changes, alongside the existing `env` sync. With no revision the form starts at the schema defaults (`standard` / cpu 1 / gpu 0, memory 512, timeout 900 or 120 for an extractor) — unchanged from today.
- **Profiles are read-only in the UI** (spec §9, §12): no editor, no "custom" option.
- Commit messages end with:
  ```
  Co-Authored-By: Claude <MODEL> <noreply@anthropic.com>
  Claude-Session: <the executing session's URL or name>
  ```

---

### Task 0: Precondition

- [ ] `git log main --oneline -20 | grep -i "k-1\|hardware"` shows the K-1 merge, and `app/src/lib/processes/hardware.ts` exports `PublicHardwareProfile`, `publicProfiles` and `hardwareBoundsError`; `app/src/pages/api/processes/hardware-profiles.ts` exists. If not, STOP — K-2 depends on K-1.

### Task 1: The client-safe profile type, the pure formatters, the hook

**Files:**
- Create: `app/src/lib/processes/hardware-public.ts`
- Modify: `app/src/lib/processes/hardware.ts` (a type-level assertion only — no runtime change)
- Modify: `app/src/lib/processes/api.ts` (one function)
- Modify: `app/src/lib/processes/queries.ts` (one hook)
- Modify: `app/src/lib/query/keys.ts:76-83` (one key)
- Test: `app/src/__tests__/processes-hardware-public.test.ts`

**Interfaces:**
- Consumes: `PublicHardwareProfile` (K-1, `hardware.ts` — `Omit<HardwareProfile, "backend">`), `ProcessHardware` (`schemas.ts`: `{profile: string; cpu: number; gpu_count: number}`), `processFetch` (`api.ts`).
- Produces (every later task imports these by name):
  ```ts
  // hardware-public.ts — NO node imports; safe in the browser bundle
  export type HardwareTier = "cpu" | "cpu-large" | "gpu";
  export interface HardwareBounds { min: number; max: number; default: number }
  export interface PublicHardwareProfile {
    id: string; label: string; description: string; tier: HardwareTier;
    accelerator: { vendor: string; model: string; memory_gb: number } | null;
    cpu: HardwareBounds; memory_mb: HardwareBounds; gpu_count: HardwareBounds | null;
    max_queue_wait_seconds: number; image: string | null;
  }
  export interface HardwareProfilesResponse { backend: string; profiles: PublicHardwareProfile[] }
  export const TIER_LABELS: Record<HardwareTier, string>;            // CPU / Large CPU / GPU
  export const TIER_ORDER: readonly HardwareTier[];                   // ["cpu", "cpu-large", "gpu"]
  export function findPublicProfile(profiles: PublicHardwareProfile[], id: string): PublicHardwareProfile | undefined;
  export function profileDefaults(p: PublicHardwareProfile): { hardware: ProcessHardware; memoryMb: number };
  export function acceleratorLabel(p: PublicHardwareProfile): string | null;   // "NVIDIA L4 · 24 GB"
  export function formatMemoryMb(mb: number): string;                 // 512 → "512 MB", 16384 → "16 GB", 1536 → "1.5 GB"
  export function formatWait(seconds: number): string;                // 0 → "no wait", 90 → "90 s", 1800 → "30 min", 7200 → "2 h", 5400 → "1.5 h"
  export function hardwareSummary(p: PublicHardwareProfile | undefined, hardware: ProcessHardware, memoryMb: number): string;
  // "4 vCPU · 16 GB · 1× L4 · waits up to 2 h"; with no profile: "standard (not in this deployment) · 1 vCPU · 512 MB"
  ```
  ```ts
  // api.ts
  export async function listHardwareProfiles(): Promise<HardwareProfilesResponse>;
  // queries.ts
  export function useHardwareProfiles(): UseQueryResult<HardwareProfilesResponse>;  // staleTime: Infinity
  // keys.ts
  processKeys.hardwareProfiles: () => ["processes", "hardware-profiles"] as const
  ```

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/processes-hardware-public.test.ts`:

```ts
// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  acceleratorLabel,
  findPublicProfile,
  formatMemoryMb,
  formatWait,
  hardwareSummary,
  profileDefaults,
  TIER_LABELS,
  TIER_ORDER,
  type PublicHardwareProfile,
} from "@/lib/processes/hardware-public";

const STANDARD: PublicHardwareProfile = {
  id: "standard",
  label: "Standard",
  description: "General purpose.",
  tier: "cpu",
  accelerator: null,
  cpu: { min: 0.25, max: 4, default: 1 },
  memory_mb: { min: 128, max: 16384, default: 512 },
  gpu_count: null,
  max_queue_wait_seconds: 1800,
  image: null,
};

const GPU_L4: PublicHardwareProfile = {
  id: "gpu-l4",
  label: "GPU — L4",
  description: "One NVIDIA L4.",
  tier: "gpu",
  accelerator: { vendor: "NVIDIA", model: "L4", memory_gb: 24 },
  cpu: { min: 2, max: 8, default: 4 },
  memory_mb: { min: 8192, max: 32768, default: 16384 },
  gpu_count: { min: 1, max: 1, default: 1 },
  max_queue_wait_seconds: 7200,
  image: "cuda",
};

describe("tier vocabulary", () => {
  it("labels the three tiers in spec order", () => {
    expect(TIER_ORDER).toEqual(["cpu", "cpu-large", "gpu"]);
    expect(TIER_LABELS).toEqual({ cpu: "CPU", "cpu-large": "Large CPU", gpu: "GPU" });
  });
});

describe("profileDefaults", () => {
  it("is the profile's default cpu / memory and gpu 0 without an accelerator", () => {
    expect(profileDefaults(STANDARD)).toEqual({
      hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
      memoryMb: 512,
    });
  });
  it("takes gpu_count.default with an accelerator", () => {
    expect(profileDefaults(GPU_L4)).toEqual({
      hardware: { profile: "gpu-l4", cpu: 4, gpu_count: 1 },
      memoryMb: 16384,
    });
  });
});

describe("formatters", () => {
  it("formats memory in MB below 1 GB and in GB above, trimming .0", () => {
    expect(formatMemoryMb(512)).toBe("512 MB");
    expect(formatMemoryMb(1024)).toBe("1 GB");
    expect(formatMemoryMb(1536)).toBe("1.5 GB");
    expect(formatMemoryMb(16384)).toBe("16 GB");
  });
  it("formats the queue wait in s / min / h", () => {
    expect(formatWait(0)).toBe("no wait");
    expect(formatWait(90)).toBe("90 s");
    expect(formatWait(1800)).toBe("30 min");
    expect(formatWait(5400)).toBe("1.5 h");
    expect(formatWait(7200)).toBe("2 h");
  });
  it("labels an accelerator and returns null without one", () => {
    expect(acceleratorLabel(GPU_L4)).toBe("NVIDIA L4 · 24 GB");
    expect(acceleratorLabel(STANDARD)).toBeNull();
  });
});

describe("hardwareSummary", () => {
  it("reads vCPU · memory · GPUs · wait for a GPU profile", () => {
    expect(hardwareSummary(GPU_L4, { profile: "gpu-l4", cpu: 4, gpu_count: 1 }, 16384)).toBe(
      "4 vCPU · 16 GB · 1× L4 · waits up to 2 h",
    );
  });
  it("omits the GPU segment when gpu_count is 0", () => {
    expect(hardwareSummary(STANDARD, { profile: "standard", cpu: 0.5, gpu_count: 0 }, 512)).toBe(
      "0.5 vCPU · 512 MB · waits up to 30 min",
    );
  });
  it("says so when the profile is not in this deployment", () => {
    expect(hardwareSummary(undefined, { profile: "gone", cpu: 1, gpu_count: 0 }, 512)).toBe(
      "gone (not in this deployment) · 1 vCPU · 512 MB",
    );
  });
});

describe("findPublicProfile", () => {
  it("finds by id", () => {
    expect(findPublicProfile([STANDARD, GPU_L4], "gpu-l4")).toBe(GPU_L4);
    expect(findPublicProfile([STANDARD], "gpu-l4")).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `app/`): `npx vitest run src/__tests__/processes-hardware-public.test.ts`
Expected: FAIL — cannot resolve `@/lib/processes/hardware-public`.

- [ ] **Step 3: Write `hardware-public.ts`**

```ts
/**
 * The hardware-profile vocabulary the UI reads (K-2, process-compute spec
 * §3.3 / §9). This is the CLIENT-SAFE half of K-1's `hardware.ts`: that
 * module reads the deployment's profile file with `node:fs` and must never
 * reach the browser bundle, so the public shape and the pure formatters live
 * here and `hardware.ts` asserts (at the type level) that what its route
 * returns is exactly this.
 */
import type { ProcessHardware } from "./schemas";

export type HardwareTier = "cpu" | "cpu-large" | "gpu";

export interface HardwareBounds {
  min: number;
  max: number;
  default: number;
}

/** One profile as `GET /api/processes/hardware-profiles` returns it — a
 * K-1 profile minus its pipeline-only `backend` block. */
export interface PublicHardwareProfile {
  id: string;
  label: string;
  description: string;
  tier: HardwareTier;
  accelerator: { vendor: string; model: string; memory_gb: number } | null;
  cpu: HardwareBounds;
  memory_mb: HardwareBounds;
  gpu_count: HardwareBounds | null;
  max_queue_wait_seconds: number;
  image: string | null;
}

export interface HardwareProfilesResponse {
  backend: string;
  profiles: PublicHardwareProfile[];
}

export const TIER_ORDER: readonly HardwareTier[] = ["cpu", "cpu-large", "gpu"];

export const TIER_LABELS: Record<HardwareTier, string> = {
  cpu: "CPU",
  "cpu-large": "Large CPU",
  gpu: "GPU",
};

export function findPublicProfile(
  profiles: PublicHardwareProfile[],
  id: string,
): PublicHardwareProfile | undefined {
  return profiles.find((p) => p.id === id);
}

/** What the form resets to when a profile is chosen (spec §9). */
export function profileDefaults(p: PublicHardwareProfile): {
  hardware: ProcessHardware;
  memoryMb: number;
} {
  return {
    hardware: { profile: p.id, cpu: p.cpu.default, gpu_count: p.gpu_count?.default ?? 0 },
    memoryMb: p.memory_mb.default,
  };
}

export function acceleratorLabel(p: PublicHardwareProfile): string | null {
  if (!p.accelerator) return null;
  return `${p.accelerator.vendor} ${p.accelerator.model} · ${p.accelerator.memory_gb} GB`;
}

function trim(n: number): string {
  // 1.5 stays 1.5; 16.0 prints as 16 — JS String() already does that.
  return String(Math.round(n * 100) / 100);
}

export function formatMemoryMb(mb: number): string {
  return mb < 1024 ? `${mb} MB` : `${trim(mb / 1024)} GB`;
}

export function formatWait(seconds: number): string {
  if (seconds <= 0) return "no wait";
  if (seconds < 120) return `${seconds} s`;
  if (seconds < 3600) return `${trim(seconds / 60)} min`;
  return `${trim(seconds / 3600)} h`;
}

/** The one-line summary under the fieldset and on a run row:
 * `4 vCPU · 16 GB · 1× L4 · waits up to 2 h`. */
export function hardwareSummary(
  p: PublicHardwareProfile | undefined,
  hardware: ProcessHardware,
  memoryMb: number,
): string {
  const parts: string[] = [];
  if (!p) parts.push(`${hardware.profile} (not in this deployment)`);
  parts.push(`${trim(hardware.cpu)} vCPU`, formatMemoryMb(memoryMb));
  if (p && hardware.gpu_count > 0) {
    parts.push(`${hardware.gpu_count}× ${p.accelerator?.model ?? "GPU"}`);
  }
  if (p) parts.push(`waits up to ${formatWait(p.max_queue_wait_seconds)}`);
  return parts.join(" · ");
}
```

- [ ] **Step 4: Pin `hardware.ts`'s public type to this one**

At the end of `app/src/lib/processes/hardware.ts` (after `publicProfiles`):

```ts
// K-2: the browser reads profiles through `hardware-public.ts` (this module
// imports node:fs). These two assignments fail to compile if the shapes drift
// in either direction.
import type { PublicHardwareProfile as ClientProfile } from "./hardware-public";
type _ServerToClient = PublicHardwareProfile extends ClientProfile ? true : never;
type _ClientToServer = ClientProfile extends PublicHardwareProfile ? true : never;
const _shapesAgree: [_ServerToClient, _ClientToServer] = [true, true];
void _shapesAgree;
```

(If the project's lint forbids a non-top-level `import type`, move it to the file's import block — the assertion is what matters.)

- [ ] **Step 5: The key, the API function, the hook**

`app/src/lib/query/keys.ts`, inside `processKeys` after `runs`:
```ts
  hardwareProfiles: () => [...processKeys.all(), "hardware-profiles"] as const,
```

`app/src/lib/processes/api.ts`, after `listRuns`:
```ts
export async function listHardwareProfiles(): Promise<HardwareProfilesResponse> {
  return processFetch<HardwareProfilesResponse>("/hardware-profiles");
}
```
with `import type { HardwareProfilesResponse } from "./hardware-public";` in the import block. (Check how `processFetch` builds its URL — `listRuns` is the model; the route is `/api/processes/hardware-profiles`.)

`app/src/lib/processes/queries.ts`, after `useRuns`:
```ts
/** The deployment's hardware profiles (K-2). Deployment config, not
 * operator data: fetched once per page and never considered stale. */
export function useHardwareProfiles() {
  return useQuery({
    queryKey: processKeys.hardwareProfiles(),
    queryFn: listHardwareProfiles,
    staleTime: Infinity,
  });
}
```
adding `listHardwareProfiles` to the `./api` import list.

- [ ] **Step 6: Run the test and the gate**

Run: `npx vitest run src/__tests__/processes-hardware-public.test.ts` → PASS (10 tests).
Run: `npm run verify` from the worktree root → green.

- [ ] **Step 7: Commit**

```bash
git add app/src/lib/processes/hardware-public.ts app/src/lib/processes/hardware.ts app/src/lib/processes/api.ts app/src/lib/processes/queries.ts app/src/lib/query/keys.ts app/src/__tests__/processes-hardware-public.test.ts
git commit -m "feat(processes): client-safe hardware profile type, formatters and useHardwareProfiles (K-2)"
```

### Task 2: `HardwareFieldset`

**Files:**
- Create: `app/src/components/processes/HardwareFieldset.tsx`
- Test: `app/src/__tests__/process-hardware-fieldset.test.tsx`

**Interfaces:**
- Consumes: everything Task 1 exports from `hardware-public.ts`; `Input`, `Label` from `@stac-higher/shared`; `Badge` from `@stac-higher/shared` (check where `ProcessDetailPage.tsx` imports them from — lines 3–16 — and import from the same place).
- Produces:
  ```tsx
  export interface HardwareFieldsetProps {
    profiles: PublicHardwareProfile[] | undefined;   // undefined while loading
    hardware: ProcessHardware;
    memoryMb: number;
    disabled?: boolean;
    onChange: (next: { hardware: ProcessHardware; memoryMb: number }) => void;
  }
  export function HardwareFieldset(props: HardwareFieldsetProps): JSX.Element;
  ```
  Controls, by accessible name (tests and the lead's Chrome check use these): select `Hardware profile`; number inputs `CPU (cores)`, `Memory (MB)`, `GPU count` (the last only when the selected profile has an accelerator); a `<p data-testid="hardware-summary">` with the summary line; the description in a `<p>` and the accelerator label in a `<Badge variant="outline">`.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/process-hardware-fieldset.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { HardwareFieldset } from "@/components/processes/HardwareFieldset";
import type { PublicHardwareProfile } from "@/lib/processes/hardware-public";

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
  id: "gpu-l4", label: "GPU — L4", description: "One NVIDIA L4.", tier: "gpu",
  accelerator: { vendor: "NVIDIA", model: "L4", memory_gb: 24 },
  cpu: { min: 2, max: 8, default: 4 }, memory_mb: { min: 8192, max: 32768, default: 16384 },
  gpu_count: { min: 1, max: 1, default: 1 }, max_queue_wait_seconds: 7200, image: "cuda",
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

  it("keeps a profile this deployment no longer has as a disabled option and says so", () => {
    setup({ hardware: { profile: "gone", cpu: 1, gpu_count: 0 } });
    const select = screen.getByLabelText("Hardware profile") as HTMLSelectElement;
    expect(select.value).toBe("gone");
    const gone = Array.from(select.options).find((o) => o.value === "gone");
    expect(gone?.disabled).toBe(true);
    expect(screen.getByTestId("hardware-summary")).toHaveTextContent("gone (not in this deployment)");
  });

  it("renders a loading placeholder while the profiles are undefined", () => {
    setup({ profiles: undefined });
    expect(screen.getByLabelText("Hardware profile")).toBeDisabled();
    expect(screen.getByText(/Loading hardware profiles/)).toBeInTheDocument();
  });

  it("disables every control when disabled", () => {
    setup({ disabled: true });
    expect(screen.getByLabelText("Hardware profile")).toBeDisabled();
    expect(screen.getByLabelText("CPU (cores)")).toBeDisabled();
    expect(screen.getByLabelText("Memory (MB)")).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `app/`): `npx vitest run src/__tests__/process-hardware-fieldset.test.tsx`
Expected: FAIL — cannot resolve `@/components/processes/HardwareFieldset`.

- [ ] **Step 3: Write the component**

`app/src/components/processes/HardwareFieldset.tsx`:

```tsx
/**
 * The deploy form's Hardware fieldset (K-2, process-compute spec §9).
 *
 * An operator picks a PROFILE — a deployment-defined bundle of tier,
 * accelerator and bounds (spec §3) — then sets cpu / memory / gpu count
 * inside that profile's bounds. The profile is the portable vocabulary: the
 * same revision asks for `gpu-l4` on Docker and on EKS and the backend maps
 * it. Native controls, like the other pickers on the process page.
 *
 * Bounds are enforced here only as input attributes — the write gate refuses
 * an out-of-bounds value with a 400 naming the bound (K-1), and the pipeline
 * re-checks at launch, so this is a convenience, not the guard.
 */
import { Badge, Input, Label } from "@stac-higher/shared";
import {
  acceleratorLabel,
  findPublicProfile,
  hardwareSummary,
  profileDefaults,
  TIER_LABELS,
  TIER_ORDER,
  type PublicHardwareProfile,
} from "@/lib/processes/hardware-public";
import type { ProcessHardware } from "@/lib/processes/schemas";

export interface HardwareFieldsetProps {
  /** `undefined` while the profiles are loading. */
  profiles: PublicHardwareProfile[] | undefined;
  hardware: ProcessHardware;
  memoryMb: number;
  disabled?: boolean;
  onChange: (next: { hardware: ProcessHardware; memoryMb: number }) => void;
}

const SELECT_CLASS =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-sm";

export function HardwareFieldset({
  profiles,
  hardware,
  memoryMb,
  disabled = false,
  onChange,
}: HardwareFieldsetProps) {
  const loaded = profiles ?? [];
  const selected = findPublicProfile(loaded, hardware.profile);
  const accelerator = selected ? acceleratorLabel(selected) : null;

  const pickProfile = (id: string) => {
    const next = findPublicProfile(loaded, id);
    if (next) onChange(profileDefaults(next));
  };
  const setCpu = (cpu: number) => onChange({ hardware: { ...hardware, cpu }, memoryMb });
  const setGpus = (gpu_count: number) =>
    onChange({ hardware: { ...hardware, gpu_count }, memoryMb });
  const setMemory = (next: number) => onChange({ hardware, memoryMb: next });

  return (
    <fieldset className="grid gap-3">
      <legend className="text-sm font-medium">Hardware</legend>
      <div className="grid gap-2">
        <Label htmlFor="hardware-profile">Hardware profile</Label>
        <select
          id="hardware-profile"
          aria-label="Hardware profile"
          className={SELECT_CLASS}
          value={hardware.profile}
          disabled={disabled || profiles === undefined}
          onChange={(e) => pickProfile(e.target.value)}
        >
          {/* A revision may pin a profile this deployment no longer defines:
              keep it selectable-looking but disabled so the form still shows
              what was deployed, and the summary says why. */}
          {!selected && (
            <option value={hardware.profile} disabled>
              {hardware.profile} (not in this deployment)
            </option>
          )}
          {TIER_ORDER.map((tier) => {
            const inTier = loaded.filter((p) => p.tier === tier);
            if (inTier.length === 0) return null;
            return (
              <optgroup key={tier} label={TIER_LABELS[tier]}>
                {inTier.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>
        {profiles === undefined ? (
          <p className="text-xs text-muted-foreground">Loading hardware profiles…</p>
        ) : (
          selected && (
            <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{selected.description}</span>
              {accelerator && <Badge variant="outline">{accelerator}</Badge>}
            </p>
          )
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="grid gap-2">
          <Label htmlFor="hardware-cpu">CPU (cores)</Label>
          <Input
            id="hardware-cpu"
            type="number"
            min={selected?.cpu.min}
            max={selected?.cpu.max}
            step={0.25}
            value={hardware.cpu}
            disabled={disabled}
            onChange={(e) => setCpu(Number(e.target.value))}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="memory">Memory (MB)</Label>
          <Input
            id="memory"
            type="number"
            min={selected?.memory_mb.min}
            max={selected?.memory_mb.max}
            step={128}
            value={memoryMb}
            disabled={disabled}
            onChange={(e) => setMemory(Number(e.target.value))}
          />
        </div>
        {selected?.gpu_count && (
          <div className="grid gap-2">
            <Label htmlFor="hardware-gpus">GPU count</Label>
            <Input
              id="hardware-gpus"
              type="number"
              min={selected.gpu_count.min}
              max={selected.gpu_count.max}
              step={1}
              value={hardware.gpu_count}
              disabled={disabled}
              onChange={(e) => setGpus(Number(e.target.value))}
            />
          </div>
        )}
      </div>
      <p data-testid="hardware-summary" className="text-xs text-muted-foreground">
        {hardwareSummary(selected, hardware, memoryMb)}
      </p>
    </fieldset>
  );
}
```

If `Badge`, `Input` or `Label` are not exported from `@stac-higher/shared` in this checkout, import them from wherever `ProcessDetailPage.tsx` does (its lines 3–16) and note the change in your report.

- [ ] **Step 4: Run the test and the gate**

Run: `npx vitest run src/__tests__/process-hardware-fieldset.test.tsx` → PASS (8 tests).
Run: `npm run verify` from the worktree root → green.

- [ ] **Step 5: Commit**

```bash
git add app/src/components/processes/HardwareFieldset.tsx app/src/__tests__/process-hardware-fieldset.test.tsx
git commit -m "feat(processes): HardwareFieldset — profile select by tier, bounded cpu/memory/gpu inputs, summary line (K-2)"
```

### Task 3: `CodeCard` picks hardware and starts from the current revision

**Files:**
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` — `CodeCard` (currently lines 241–424) and the `CodeCard` call in `ProcessDetailContent` (~line 1138)
- Test: `app/src/__tests__/process-code-card.test.tsx` (extend)

**Interfaces:**
- Consumes: `HardwareFieldset` (Task 2), `useHardwareProfiles` (Task 1), `processRuntimeReadSchema` / `ProcessRuntime` / `ProcessHardware` (`schemas.ts`).
- Produces: `CodeCard` gains the prop `currentRuntime: ProcessRuntime | null` (the current revision's parsed runtime, `null` with no revision or an unparseable one). Deploy payload `runtime.hardware` is the form's state; `runtime.memory_mb` too.

- [ ] **Step 1: Extend the existing test file — mocks first**

In `app/src/__tests__/process-code-card.test.tsx` the `@/lib/processes/queries` mock must now also provide the hook, and `setup` must pass the new prop. Replace the mock and `setup`:

```tsx
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
```

```tsx
import type { ProcessRuntime } from "@/lib/processes/schemas";

function setup(
  kind: "transform" | "extractor" = "transform",
  currentRuntime: ProcessRuntime | null = null,
  currentRevision: string | null = null,
) {
  return render(
    <CodeCard
      id={PROCESS_ID}
      groupId="earth-observation"
      currentCode="print(1)"
      currentEnv={[]}
      currentRevision={currentRevision}
      currentRuntime={currentRuntime}
      canMutate
      kind={kind}
    />,
  );
}
```

Then append the new cases:

```tsx
const DEPLOYED: ProcessRuntime = {
  kind: "inline_python",
  image: null,
  memory_mb: 8192,
  timeout_seconds: 3600,
  retry: { max_attempts: 3, backoff: "exponential" },
  network: { level: "isolated", hosts: [] },
  runtime_image: "default",
  hardware: { profile: "cpu-large", cpu: 4, gpu_count: 0 },
};

describe("CodeCard hardware (K-2, spec §9)", () => {
  it("deploys the schema defaults when there is no revision", async () => {
    setup();
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("standard");
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(512);
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const { input } = mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: { hardware: unknown; memory_mb: number; timeout_seconds: number } };
    };
    expect(input.runtime.hardware).toEqual({ profile: "standard", cpu: 1, gpu_count: 0 });
    expect(input.runtime.memory_mb).toBe(512);
    expect(input.runtime.timeout_seconds).toBe(900);
  });

  it("starts from the current revision's hardware, memory and timeout", () => {
    setup("transform", DEPLOYED, "rev-1");
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("cpu-large");
    expect(screen.getByLabelText("CPU (cores)")).toHaveValue(4);
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
    expect(screen.getByLabelText("Timeout (seconds)")).toHaveValue(3600);
  });

  it("re-syncs when the current revision moves", () => {
    const view = setup("transform", null, null);
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(512);
    view.rerender(
      <CodeCard
        id={PROCESS_ID}
        groupId="earth-observation"
        currentCode="print(1)"
        currentEnv={[]}
        currentRevision="rev-2"
        currentRuntime={DEPLOYED}
        canMutate
        kind="transform"
      />,
    );
    expect(screen.getByLabelText("Hardware profile")).toHaveValue("cpu-large");
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
    expect(screen.getByLabelText("Timeout (seconds)")).toHaveValue(3600);
  });

  it("deploys the picked profile at its defaults after a profile change", async () => {
    setup();
    fireEvent.change(screen.getByLabelText("Hardware profile"), { target: { value: "cpu-large" } });
    expect(screen.getByLabelText("CPU (cores)")).toHaveValue(4);
    expect(screen.getByLabelText("Memory (MB)")).toHaveValue(8192);
    fireEvent.click(screen.getByRole("button", { name: /Deploy revision/ }));
    await waitFor(() => expect(mutateAsync).toHaveBeenCalledTimes(1));
    const { input } = mutateAsync.mock.calls[0][0] as unknown as {
      input: { runtime: { hardware: unknown; memory_mb: number } };
    };
    expect(input.runtime.hardware).toEqual({ profile: "cpu-large", cpu: 4, gpu_count: 0 });
    expect(input.runtime.memory_mb).toBe(8192);
  });
});
```

The pre-existing timeout-default test (`defaults an extractor's timeout to 120 s and a transform's to 900 s`) must keep passing unchanged — it exercises the no-revision path.

- [ ] **Step 2: Run to verify the new cases fail**

Run (from `app/`): `npx vitest run src/__tests__/process-code-card.test.tsx`
Expected: the four new cases FAIL (no `Hardware profile` control; typecheck complains about `currentRuntime`); the old cases still pass or fail only on the missing prop.

- [ ] **Step 3: Change `CodeCard`**

In `ProcessDetailPage.tsx`:

1. Imports: add `import { HardwareFieldset } from "@/components/processes/HardwareFieldset";`, add `useHardwareProfiles` to the `@/lib/processes/queries` import, and add `ProcessHardware` and `ProcessRuntime` to the `@/lib/processes/schemas` type import (`processRuntimeReadSchema` as a value import — see step 4).

2. Props: add `currentRuntime: ProcessRuntime | null;` to `CodeCard`'s props (after `currentRevision`).

3. State — replace the `memoryMb` / `timeoutSeconds` declarations with:

```tsx
  const DEFAULT_HARDWARE: ProcessHardware = { profile: "standard", cpu: 1, gpu_count: 0 };
  // GOES spec §6.5: an extractor runs against one ingested file rather than a
  // batch, so it defaults to a much shorter timeout than a transform.
  const defaultTimeout = kind === "extractor" ? 120 : 900;
  const [hardware, setHardware] = useState<ProcessHardware>(
    currentRuntime?.hardware ?? DEFAULT_HARDWARE,
  );
  const [memoryMb, setMemoryMb] = useState(currentRuntime?.memory_mb ?? 512);
  const [timeoutSeconds, setTimeoutSeconds] = useState(
    currentRuntime?.timeout_seconds ?? defaultTimeout,
  );
  const { data: hardwareProfiles } = useHardwareProfiles();
```

(Move `DEFAULT_HARDWARE` to module level, above `CodeCard`, so it is not re-created per render.)

4. Sync — extend the existing `currentRevision` effect so a deploy starts from what is deployed, not from 512/900:

```tsx
  // A deploy starts from what is deployed: re-sync when the current revision
  // moves, so the form is an edit of the live env / hardware / limits rather
  // than a blank slate that would silently drop them (spec §9: every deploy
  // form used to start at 512/900 regardless of the revision).
  useEffect(() => {
    setEnv(currentEnv);
    setHardware(currentRuntime?.hardware ?? DEFAULT_HARDWARE);
    setMemoryMb(currentRuntime?.memory_mb ?? 512);
    setTimeoutSeconds(currentRuntime?.timeout_seconds ?? defaultTimeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentRevision]);
```

5. Payload — in `deploy()`, replace the `hardware: { profile: "standard", cpu: 1, gpu_count: 0 },` line and its K-1 comment with `hardware,` (memory_mb already reads `memoryMb`).

6. Markup — replace the `<div className="grid gap-4 sm:grid-cols-2">` block holding the Memory and Timeout inputs with:

```tsx
        <HardwareFieldset
          profiles={hardwareProfiles?.profiles}
          hardware={hardware}
          memoryMb={memoryMb}
          disabled={!canMutate}
          onChange={(next) => {
            setHardware(next.hardware);
            setMemoryMb(next.memoryMb);
          }}
        />
        <div className="grid gap-2 sm:max-w-sm">
          <Label htmlFor="timeout">Timeout (seconds)</Label>
          <Input
            id="timeout"
            type="number"
            min={1}
            max={86400}
            value={timeoutSeconds}
            disabled={!canMutate}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
          />
        </div>
```

(The `86400` literal is `TIMEOUT_SECONDS_MAX` in `schemas.ts` — import and use that constant if it is exported; otherwise leave the literal, it is not a profile bound.)

- [ ] **Step 4: Pass `currentRuntime` from the page**

In `ProcessDetailContent`, after `const current = …` add:

```tsx
  // The revision's runtime is stored jsonb; the read schema applies the
  // defaults an old revision lacks (hardware, runtime_image), so the form
  // sees one shape. An unparseable runtime falls back to the form defaults.
  const currentRuntime: ProcessRuntime | null = current
    ? (processRuntimeReadSchema.safeParse(current.runtime).data ?? null)
    : null;
```

and pass `currentRuntime={currentRuntime}` to the `CodeCard` element (the non-builtin branch). Check whether `BuiltinCard` also renders a `CodeCard`-like deploy — it does not; leave it.

- [ ] **Step 5: Run the tests and the gate**

Run: `npx vitest run src/__tests__/process-code-card.test.tsx` → PASS (all cases, old and new).
Run: `npm run verify` from the worktree root → green.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/processes/ProcessDetailPage.tsx app/src/__tests__/process-code-card.test.tsx
git commit -m "feat(processes): deploy form picks hardware and starts from the current revision's hardware/memory/timeout (K-2)"
```

### Task 4: Run rows show the pinned hardware; docs

**Files:**
- Modify: `app/src/components/processes/ProcessDetailPage.tsx` — `RunRow` (~line 745) and `RunsCard` (~line 830), the `RunsCard` call in `ProcessDetailContent`
- Modify: `docs/processes.md` — new `## Hardware` section after `## Runtime image`
- Test: `app/src/__tests__/process-run-row-hardware.test.tsx`

**Interfaces:**
- Consumes: `hardwareSummary`, `findPublicProfile` (Task 1); `useRevisions`, `useHardwareProfiles` (existing / Task 1); `processRuntimeReadSchema`.
- Produces: `RunRow` gains `hardware: string | null` (the summary text, `null` when the revision is unknown); `RunsCard` gains `revisions: ProcessRevision[] | undefined` and computes the text per run. A run row renders the text in a `<span data-testid="run-hardware">` after the attempt count.

- [ ] **Step 1: Write the failing test**

`app/src/__tests__/process-run-row-hardware.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/lib/processes/queries", () => ({
  useRuns: () => ({ data: RUNS, isLoading: false }),
  useRerunRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useHardwareProfiles: () => ({ data: { backend: "docker", profiles: PROFILES } }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { RunsCard } from "@/components/processes/ProcessDetailPage";

const PROFILES = [
  {
    id: "standard", label: "Standard", description: "General purpose.", tier: "cpu",
    accelerator: null, cpu: { min: 0.25, max: 4, default: 1 },
    memory_mb: { min: 128, max: 16384, default: 512 }, gpu_count: null,
    max_queue_wait_seconds: 1800, image: null,
  },
];

const REVISIONS = [
  {
    id: "rev-old", process_id: "p1", code: null, env: [], created_by: "x", created_at: "2026-09-01T00:00:00Z",
    // Predates K-1: no hardware block — the read schema defaults it.
    runtime: { kind: "inline_python", image: null, memory_mb: 512, timeout_seconds: 900 },
  },
  {
    id: "rev-new", process_id: "p1", code: null, env: [], created_by: "x", created_at: "2026-09-02T00:00:00Z",
    runtime: {
      kind: "inline_python", image: null, memory_mb: 2048, timeout_seconds: 900,
      hardware: { profile: "standard", cpu: 2, gpu_count: 0 },
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
  { ...baseRun, id: "run-3", revision_id: "rev-missing" },
];

describe("RunRow pinned hardware (K-2, spec §9)", () => {
  it("summarises each run's pinned revision and says nothing for an unknown one", () => {
    render(<RunsCard id="p1" canMutate={false} isExtractor={false} revisions={REVISIONS as never} />);
    const cells = screen.getAllByTestId("run-hardware").map((el) => el.textContent);
    expect(cells).toEqual([
      "2 vCPU · 2 GB · waits up to 30 min",
      "1 vCPU · 512 MB · waits up to 30 min",
    ]);
  });
});
```

(`RunsCard` must be exported for this — add `export` to its declaration. If `RunsCard` renders anything else that needs a mock — check its imports — mock it the same way; report what you added.)

- [ ] **Step 2: Run to verify it fails**

Run (from `app/`): `npx vitest run src/__tests__/process-run-row-hardware.test.tsx`
Expected: FAIL — `RunsCard` is not exported / `run-hardware` not found.

- [ ] **Step 3: Implement**

In `ProcessDetailPage.tsx`:

1. `RunsCard`: `export function RunsCard({ id, canMutate, isExtractor, revisions }: { id: string; canMutate: boolean; isExtractor: boolean; revisions: ProcessRevision[] | undefined })`. Inside, after `useRuns`:

```tsx
  const { data: hardwareProfiles } = useHardwareProfiles();
  // A run pins the revision that executed it; the revision's runtime carries
  // the hardware it asked for. Both lists are already on the page.
  const hardwareFor = (revisionId: string): string | null => {
    const revision = revisions?.find((r) => r.id === revisionId);
    if (!revision) return null;
    const runtime = processRuntimeReadSchema.safeParse(revision.runtime).data;
    if (!runtime) return null;
    const profile = findPublicProfile(hardwareProfiles?.profiles ?? [], runtime.hardware.profile);
    return hardwareSummary(profile, runtime.hardware, runtime.memory_mb);
  };
```

and pass `hardware={hardwareFor(run.revision_id)}` to each `<RunRow>`.

2. `RunRow`: add `hardware: string | null` to its props; in the `text-sm text-muted-foreground` line after the attempt/published text, render:

```tsx
          {hardware && (
            <>
              {" · "}
              <span data-testid="run-hardware">{hardware}</span>
            </>
          )}
```

3. `ProcessDetailContent`: pass `revisions={revisions}` to `<RunsCard>` (the `useRevisions(id)` result is already in scope as `revisions`).

Add `findPublicProfile`, `hardwareSummary` to the imports from `@/lib/processes/hardware-public` and `ProcessRevision` to the `@/lib/processes/types` type import if not already there.

- [ ] **Step 4: Docs**

`docs/processes.md` — insert after the `## Runtime image` section (before `## Network access`):

```markdown
## Hardware

A revision names the hardware its runs ask for as a **profile** plus numbers
inside that profile's bounds:

```json
"hardware": { "profile": "standard", "cpu": 1, "gpu_count": 0 }
```

with `memory_mb` and `timeout_seconds` beside it at the top level of
`runtime`. Profiles are defined per deployment (an operator picks; nobody
edits them in the UI) and listed by `GET /api/processes/hardware-profiles`:
each has a tier (CPU, Large CPU, GPU), an optional accelerator, inclusive
`cpu` / `memory_mb` / `gpu_count` bounds with defaults, and the longest a
run will wait for capacity. The deploy form's Hardware fieldset groups them
by tier, bounds the inputs and resets the numbers to the profile's defaults
when you change profile; a revision stored before profiles existed reads as
`standard` at 1 CPU, no GPU.

Out-of-bounds values are refused twice: at deploy (400 naming the bound) and
again by the pipeline at launch (the run dies with the same message), so a
profile set that shrinks after a deploy cannot silently launch an oversized
run. Every run row shows the hardware its pinned revision asked for. The
Docker backend applies `cpu` and the profile's device requests from K-3 on;
until then the numbers are recorded and enforced against the bounds only.
```

- [ ] **Step 5: Run the tests and the gate**

Run: `npx vitest run src/__tests__/process-run-row-hardware.test.tsx src/__tests__/process-code-card.test.tsx` → PASS.
Run: `npm run verify` from the worktree root → green.

- [ ] **Step 6: Commit**

```bash
git add app/src/components/processes/ProcessDetailPage.tsx app/src/__tests__/process-run-row-hardware.test.tsx docs/processes.md
git commit -m "feat(processes): run rows show the pinned revision's hardware; docs/processes.md Hardware section (K-2)"
```

### Task 5: Verify, merge, live check (lead only)

- [ ] `npm run verify` on the worktree; rebase onto `main`, verify again, push, open the PR (`Closes #10`), squash-merge when CI is green.
- [ ] e2e: `processes` spec, then the whole suite (`run-e2e` skill; baseline 46 passed / 1 skipped; `E2E_PORT=4399` if :4321 is held).
- [ ] Chrome: open a process page on the dev server; the Hardware fieldset shows `Standard` / `CPU — large` under their tier groups, the summary line updates on a profile change, a deploy with the cpu typed as `9` on `standard` is refused with the write gate's message in the toast; a run row shows its summary. Screenshot noted in the landed note.
- [ ] PR body = the landed note (deviations, the Chrome check); `docs/FEATURES.md` process-compute entry; `docs/ISSUES.md` for any gap; worktree removal.

## Self-review

- Spec §9 deploy-form bullet: select by tier (T2), description + accelerator badge (T2), bounded cpu/memory (T2 — the `128`/`86400` literals: memory's `128` is gone, timeout's `86400` stays because timeout is not a profile bound — noted in T3), GPU count only with an accelerator (T2), summary line (T1/T2), reset on profile change (T2), sync of hardware/memory/timeout on load (T3). Run-rows hardware summary (T4). The `phase` chip / Cancel / `cancelled` / TestRunCard phases / health `cancelled` are K-3/K-4 by the TODO's slice text — not here. Docs "Hardware" section (T4).
- Type consistency: `PublicHardwareProfile`, `profileDefaults`, `hardwareSummary`, `findPublicProfile`, `TIER_ORDER`, `TIER_LABELS`, `acceleratorLabel` are defined in T1 and used with the same signatures in T2–T4; `HardwareFieldsetProps.onChange` returns `{hardware, memoryMb}` in T2 and is consumed that way in T3; `RunsCard.revisions` and `RunRow.hardware` in T4 only.
- Placeholders: none — every step has its code and its command.
