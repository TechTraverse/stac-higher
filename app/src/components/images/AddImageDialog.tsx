/**
 * "Add image" (C-3, container-images spec §9.2/§9.3): the same dialog on
 * the /images dashboard and in the deploy form's picker.
 *
 * reference (+ optional tag, + optional registry credential) -> 202 ->
 * live status by polling the scan, until `approved` (selectable: "Use this
 * image") or `rejected` (the policy reasons verbatim, and "ask an admin for
 * an exception"). The typed reference is previewed in its stored form using
 * the same normalizer the route runs. Without a scanner (C-2 not deployed)
 * the image stays "Waiting for scan" and the dialog says it can be closed.
 */
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, Input, Label, LoadingState } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useConnections } from "@/lib/connections/queries";
import { normalizeImageInput } from "@/lib/images/normalize";
import { useAddImage, useImagePolicy, useImageScan } from "@/lib/images/queries";
import type { Image } from "@/lib/images/types";
import { readVerdict } from "@/lib/images/verdict";
import { imageUsableReason } from "./picker";
import { ImageStatusBadge } from "./ImageStatusBadge";
import { scanError, shortDigest } from "./format";

const formSchema = z.object({
  reference: z.string().trim().min(1, "Enter an image reference"),
  tag: z.string(),
  registry_connection_id: z.string(),
});
type FormValues = z.infer<typeof formSchema>;

export function AddImageDialog({
  open,
  onOpenChange,
  groupId,
  onUse,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Limit the credential list to this group's registry connections (the
   * deploy form passes the process's group). */
  groupId?: string;
  /** Offered once the image is approved: the deploy form selects it. */
  onUse?: (image: Image) => void;
}) {
  const { data: connections } = useConnections();
  const { data: policy } = useImagePolicy();
  const addMutation = useAddImage();
  const [pending, setPending] = useState<{ imageId: string; scanId: string } | null>(null);
  const { data: polled } = useImageScan(pending?.imageId ?? null, pending?.scanId ?? null);

  const form = useForm<FormValues>({
    // Zod v4 inference vs zodResolver: the repo's known cast (project-conventions).
    resolver: zodResolver(formSchema) as any,
    defaultValues: { reference: "", tag: "", registry_connection_id: "" },
  });
  const reference = form.watch("reference");
  const tag = form.watch("tag");
  const preview = reference.trim() ? normalizeImageInput(reference, tag || null) : null;
  const registryConnections = (connections ?? []).filter(
    (c) => c.protocol === "registry" && (!groupId || c.group_id === groupId),
  );

  const submit = form.handleSubmit(async (values) => {
    try {
      const added = await addMutation.mutateAsync({
        reference: values.reference,
        // A whitespace-only tag is "no tag", never sent as an empty or
        // blank string on the wire.
        tag: values.tag.trim() || undefined,
        registry_connection_id: values.registry_connection_id || null,
      });
      setPending({ imageId: added.image_id, scanId: added.scan_id });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not add the image");
    }
  });

  const image = polled?.image ?? null;
  const scan = polled?.scan ?? null;
  const reasons = readVerdict(image?.verdict)?.reasons ?? [];
  // Approved is necessary but not sufficient: an expired exception (or, once
  // scanned, a deleted/foreign-group credential) can still make the image
  // unusable by this group. Only checked when we know the group (the deploy
  // form passes it); the dashboard's own dialog has no group to check.
  const unusableReason = image && groupId ? imageUsableReason(image, groupId) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add image</DialogTitle>
          <DialogDescription>
            The image is scanned (SBOM and vulnerabilities) before anything can
            run it, and only the digest the scan pins ever runs.
          </DialogDescription>
        </DialogHeader>

        {pending === null ? (
          <form onSubmit={submit} className="grid gap-3">
            <div className="grid gap-1.5">
              <Label htmlFor="image-reference">Image reference</Label>
              <Input
                id="image-reference"
                placeholder="ghcr.io/org/tool:1.2 or python:3.12-slim"
                autoComplete="off"
                {...form.register("reference")}
              />
              {form.formState.errors.reference && (
                <p className="text-xs text-destructive">{form.formState.errors.reference.message}</p>
              )}
              {preview &&
                (preview.ok ? (
                  <p className="text-xs text-muted-foreground">
                    Stored as <code className="tech">{`${preview.reference}:${preview.tag}`}</code>
                  </p>
                ) : (
                  <p className="text-xs text-destructive">{preview.error}</p>
                ))}
              {policy && (
                <p className="text-xs text-muted-foreground">
                  Allowed registries: {policy.allowed_registries.join(", ")}
                </p>
              )}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="image-tag">Tag (optional)</Label>
              <Input id="image-tag" placeholder="latest" autoComplete="off" {...form.register("tag")} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="registry-connection">Registry credential</Label>
              <select
                id="registry-connection"
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
                {...form.register("registry_connection_id")}
              >
                <option value="">None: a public image</option>
                {registryConnections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {`${c.name} (${String(c.config.host ?? "")})`}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                An image pulled with a group&apos;s credential can be used only by
                that group&apos;s processes.
              </p>
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={addMutation.isPending || (preview !== null && !preview.ok)}
              >
                {addMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Add and scan
              </Button>
            </DialogFooter>
          </form>
        ) : (
          <div
            className="grid gap-3 text-sm"
            data-testid="add-image-progress"
            aria-live="polite"
            role="status"
          >
            {image ? (
              <div className="flex flex-wrap items-center gap-2">
                <code className="tech">{`${image.reference}:${image.tag_at_add}`}</code>
                <ImageStatusBadge status={image.status} />
              </div>
            ) : (
              <LoadingState message="Waiting for the scan request…" />
            )}
            {(image?.status === "pending" || image?.status === "scanning") && (
              <p className="text-muted-foreground">
                Queued for scanning{scan?.status === "running" ? " (running now)" : ""}. You can
                close this dialog: the image stays on the Images page and its scan continues.
              </p>
            )}
            {image?.status === "approved" && (
              <p>
                Approved: digest <code className="tech">{shortDigest(image.digest)}</code> passed
                this deployment&apos;s policy.
              </p>
            )}
            {image?.status === "rejected" && (
              <div className="grid gap-1.5">
                <p>The scan found issues this deployment&apos;s policy blocks:</p>
                <ul className="list-disc pl-5">
                  {reasons.map((reason) => (
                    <li key={reason}>
                      <code className="tech">{reason}</code>
                    </li>
                  ))}
                </ul>
                <p className="text-muted-foreground">
                  Fix the image and add it again, or ask an admin for an exception.
                </p>
              </div>
            )}
            {image?.status === "scan_failed" && (
              <p className="text-destructive">
                The scan failed: {scanError(scan) ?? "no message recorded"}. Rescan it from the
                Images page.
              </p>
            )}
            <DialogFooter>
              {image?.status === "approved" && onUse && (
                unusableReason === null ? (
                  <Button onClick={() => onUse(image)}>Use this image</Button>
                ) : (
                  <p className="text-sm text-muted-foreground">{unusableReason}</p>
                )
              )}
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
