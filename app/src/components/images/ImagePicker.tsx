/**
 * The deploy form's image picker (C-3, container-images spec §9.3):
 * approved, fresh images this process's group may use are selectable.
 * Flagged, stale, pending ones and those pulled with another group's (or a
 * deleted) credential are listed disabled with the reason. "Add image…"
 * opens the same dialog as the dashboard. A current revision's image that
 * is no longer listed stays visible, disabled, so the form never silently
 * shows a different image than the one deployed.
 */
import { useId, useState } from "react";
import { Button, Input, Label } from "@stac-higher/shared";
import { Plus } from "lucide-react";
import type { ImageSnapshot } from "@/lib/images/reference";
import type { Image } from "@/lib/images/types";
import { shortDigest } from "./format";
import { imagePickerOptions } from "./picker";

export function ImagePicker({
  images,
  groupId,
  value,
  onChange,
  onAdd,
  disabled = false,
}: {
  images: readonly Image[];
  groupId: string;
  value: ImageSnapshot | null;
  onChange: (image: ImageSnapshot | null) => void;
  onAdd: () => void;
  disabled?: boolean;
}) {
  const uid = useId();
  const [search, setSearch] = useState("");
  const options = imagePickerOptions(images, groupId, search);
  // Whether the CURRENTLY DEPLOYED image dropped off the filtered list
  // because the search text hides it (still selectable, just filtered) or
  // because it is genuinely gone (revoked, or no longer returned at all):
  // those need different messages, so the check re-runs the picker with NO
  // search to tell them apart.
  const unfilteredOptions = imagePickerOptions(images, groupId);
  const missing = value !== null && !options.some((option) => option.id === value.id);
  const hiddenByFilter =
    missing && unfilteredOptions.some((option) => option.id === value?.id);
  const anyListed = images.some((image) => image.status !== "revoked");

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="grid min-w-0 flex-1 gap-1.5">
          <Label htmlFor={`${uid}-image`}>Image</Label>
          <select
            id={`${uid}-image`}
            className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm disabled:cursor-not-allowed disabled:opacity-50"
            value={value?.id ?? ""}
            disabled={disabled}
            onChange={(e) =>
              onChange(options.find((option) => option.id === e.target.value)?.snapshot ?? null)
            }
          >
            <option value="">Choose an approved image</option>
            {missing && value && (
              <option value={value.id} disabled>
                {`${value.reference} · ${shortDigest(value.digest)} (${
                  hiddenByFilter ? "selected; hidden by the filter" : "no longer listed; not selectable"
                })`}
              </option>
            )}
            {options.map((option) => (
              <option key={option.id} value={option.id} disabled={option.disabledReason !== null}>
                {option.disabledReason ? `${option.label} (${option.disabledReason})` : option.label}
              </option>
            ))}
          </select>
        </div>
        <Button type="button" variant="outline" onClick={onAdd} disabled={disabled}>
          <Plus className="h-4 w-4" />
          Add image…
        </Button>
      </div>
      <Input
        aria-label="Find an image"
        placeholder="Filter images by reference or tag"
        value={search}
        disabled={disabled}
        onChange={(e) => setSearch(e.target.value)}
      />
      {!anyListed && (
        <p className="text-xs text-muted-foreground">
          No images yet. Add one: it is scanned before it can be chosen.
        </p>
      )}
    </div>
  );
}
