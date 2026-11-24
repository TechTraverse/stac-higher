/**
 * The deploy form's Hardware fieldset (K-2, process-compute spec §9).
 *
 * An operator picks a PROFILE — a deployment-defined bundle of tier,
 * accelerator and bounds (spec §3) — then sets cpu / memory / gpu count
 * inside that profile's bounds. The profile is the portable vocabulary: the
 * same revision asks for `gpu-l4` on Docker and on EKS and the backend maps
 * it. Native controls, like the other pickers on the process page.
 *
 * The fieldset runs the write gate's own bounds check (`hardwareFormIssue`,
 * shared with the route through `hardware-public.ts`) and shows its message
 * inline, so a value loaded from an older revision that its profile no longer
 * admits is visible before a deploy rather than only as a dead run (I-130).
 * The gate and the pipeline's launch re-check stay the enforcement.
 */
import { useId } from "react";
import { Badge, Input, Label } from "@stac-higher/shared";
import {
  acceleratorLabel,
  findPublicProfile,
  hardwareFormIssue,
  hardwareSummary,
  profileDefaults,
  TIER_LABELS,
  TIER_ORDER,
  type HardwareField,
  type PublicHardwareProfile,
} from "@/lib/processes/hardware-public";
import type { ProcessHardware } from "@/lib/processes/schemas";

export interface HardwareFieldsetProps {
  /** `undefined` while the profiles are loading, or when they failed to. */
  profiles: PublicHardwareProfile[] | undefined;
  /** Why the profiles could not be loaded, when they could not. */
  profilesError?: string | null;
  hardware: ProcessHardware;
  memoryMb: number;
  disabled?: boolean;
  onChange: (next: { hardware: ProcessHardware; memoryMb: number }) => void;
}

const SELECT_CLASS =
  "flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50 aria-[invalid=true]:border-destructive sm:max-w-sm";

export function HardwareFieldset({
  profiles,
  profilesError = null,
  hardware,
  memoryMb,
  disabled = false,
  onChange,
}: HardwareFieldsetProps) {
  const uid = useId();
  const loading = profiles === undefined && !profilesError;
  const loaded = profiles ?? [];
  const selected = findPublicProfile(loaded, hardware.profile);
  const accelerator = selected ? acceleratorLabel(selected) : null;
  // Only against a loaded set: without one there is nothing to check against,
  // and the write gate still checks at deploy.
  const issue = profiles ? hardwareFormIssue(hardware, memoryMb, profiles) : null;
  const ids = {
    profile: `${uid}-profile`,
    about: `${uid}-about`,
    cpu: `${uid}-cpu`,
    memory: `${uid}-memory`,
    gpus: `${uid}-gpus`,
    issue: `${uid}-issue`,
  };
  /** Marks the input the issue is about and points it at the message. */
  const invalid = (field: HardwareField) =>
    issue?.field === field
      ? { "aria-invalid": true as const, "aria-describedby": ids.issue }
      : {};

  const pickProfile = (id: string) => {
    const next = findPublicProfile(loaded, id);
    if (next) onChange(profileDefaults(next));
  };
  const setCpu = (cpu: number) => onChange({ hardware: { ...hardware, cpu }, memoryMb });
  const setGpus = (gpu_count: number) =>
    onChange({ hardware: { ...hardware, gpu_count }, memoryMb });
  const setMemory = (next: number) => onChange({ hardware, memoryMb: next });

  const profileDescribedBy = [
    profilesError || loading || selected ? ids.about : null,
    issue?.field === "profile" ? ids.issue : null,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <fieldset className="grid gap-3 rounded-md border border-border p-3" disabled={disabled}>
      <legend className="px-1 text-sm font-medium">Hardware</legend>
      <div className="grid gap-2">
        <Label htmlFor={ids.profile}>Hardware profile</Label>
        <select
          id={ids.profile}
          className={SELECT_CLASS}
          value={hardware.profile}
          disabled={disabled || profiles === undefined}
          aria-describedby={profileDescribedBy || undefined}
          aria-invalid={issue?.field === "profile" || undefined}
          onChange={(e) => pickProfile(e.target.value)}
        >
          {/* A revision may pin a profile this deployment no longer defines:
              keep it as a disabled option so the form still shows what was
              deployed, and the summary says why. Without a loaded set, the
              current id is the only option and nothing is claimed about it. */}
          {!selected && (
            <option value={hardware.profile} disabled={profiles !== undefined}>
              {profiles === undefined
                ? hardware.profile
                : `${hardware.profile} (not in this deployment)`}
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
        {profilesError ? (
          <p id={ids.about} className="text-xs text-destructive">
            Could not load the hardware profiles ({profilesError}). A deploy is
            still checked against them.
          </p>
        ) : loading ? (
          <p id={ids.about} className="text-xs text-muted-foreground">
            Loading hardware profiles…
          </p>
        ) : (
          selected && (
            <p id={ids.about} className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <span>{selected.description}</span>
              {accelerator && <Badge variant="outline">{accelerator}</Badge>}
            </p>
          )
        )}
      </div>
      {/* The profile document bounds each number but has no step field: these
          steps are UI conveniences (quarter cores, 128 MB, whole GPUs). */}
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="grid gap-2">
          <Label htmlFor={ids.cpu}>CPU (cores)</Label>
          <Input
            id={ids.cpu}
            type="number"
            min={selected?.cpu.min}
            max={selected?.cpu.max}
            step={0.25}
            value={hardware.cpu}
            disabled={disabled}
            {...invalid("cpu")}
            onChange={(e) => setCpu(Number(e.target.value))}
          />
        </div>
        <div className="grid gap-2">
          <Label htmlFor={ids.memory}>Memory (MB)</Label>
          <Input
            id={ids.memory}
            type="number"
            min={selected?.memory_mb.min}
            max={selected?.memory_mb.max}
            step={128}
            value={memoryMb}
            disabled={disabled}
            {...invalid("memory_mb")}
            onChange={(e) => setMemory(Number(e.target.value))}
          />
        </div>
        {selected?.gpu_count && (
          <div className="grid gap-2">
            <Label htmlFor={ids.gpus}>GPU count</Label>
            <Input
              id={ids.gpus}
              type="number"
              min={selected.gpu_count.min}
              max={selected.gpu_count.max}
              step={1}
              value={hardware.gpu_count}
              disabled={disabled}
              {...invalid("gpu_count")}
              onChange={(e) => setGpus(Number(e.target.value))}
            />
          </div>
        )}
      </div>
      {issue && (
        <p id={ids.issue} role="alert" data-testid="hardware-issue" className="text-xs text-destructive">
          {issue.message}
        </p>
      )}
      <p
        data-testid="hardware-summary"
        aria-live="polite"
        className="text-xs text-muted-foreground"
      >
        {hardwareSummary(selected, hardware, memoryMb, { profilesLoaded: profiles !== undefined })}
      </p>
    </fieldset>
  );
}
