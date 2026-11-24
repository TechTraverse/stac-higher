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
import { useId } from "react";
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
  const uid = useId();
  const loading = profiles === undefined;
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
    <fieldset className="grid gap-3 rounded-md border border-border p-3" disabled={disabled}>
      <legend className="px-1 text-sm font-medium">Hardware</legend>
      <div className="grid gap-2">
        <Label htmlFor={`${uid}-profile`}>Hardware profile</Label>
        <select
          id={`${uid}-profile`}
          className={SELECT_CLASS}
          value={hardware.profile}
          disabled={disabled || loading}
          onChange={(e) => pickProfile(e.target.value)}
        >
          {/* A revision may pin a profile this deployment no longer defines:
              keep it as a disabled option so the form still shows what was
              deployed, and the summary says why. While loading, the current
              id is the only option and nothing is claimed about it. */}
          {!selected && (
            <option value={hardware.profile} disabled={!loading}>
              {loading ? hardware.profile : `${hardware.profile} (not in this deployment)`}
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
        {loading ? (
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
          <Label htmlFor={`${uid}-cpu`}>CPU (cores)</Label>
          <Input
            id={`${uid}-cpu`}
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
          <Label htmlFor={`${uid}-memory`}>Memory (MB)</Label>
          <Input
            id={`${uid}-memory`}
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
            <Label htmlFor={`${uid}-gpus`}>GPU count</Label>
            <Input
              id={`${uid}-gpus`}
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
        {hardwareSummary(selected, hardware, memoryMb, { profilesLoaded: !loading })}
      </p>
    </fieldset>
  );
}
