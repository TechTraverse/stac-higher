/**
 * The admin verbs on one image (C-4, container-images spec §4.4, §9.2):
 * grant an expiring exception (or replace the one an approved image
 * carries), and revoke. Rendered only for admins; the routes re-check the
 * role and the policy's `exception_max_days`. An exception never covers
 * staleness, and every grant is audited. Revoke is terminal and ends any
 * exception.
 */
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, Input, Label, Textarea } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Ban, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { useGrantImageException, useImagePolicy, useRevokeImage } from "@/lib/images/queries";
import { IMAGE_EXCEPTION_REASON_MIN } from "@/lib/images/schemas";
import type { Image } from "@/lib/images/types";
import { canTakeException, exceptionExpiry } from "./format";

/** Spec §7.1's default `exception_max_days`, used until the policy loads. */
const FALLBACK_MAX_DAYS = 90;
const DEFAULT_DAYS = 30;

function exceptionFormSchema(maxDays: number) {
  return z.object({
    reason: z
      .string()
      .trim()
      .min(IMAGE_EXCEPTION_REASON_MIN, `At least ${IMAGE_EXCEPTION_REASON_MIN} characters`)
      .max(2000),
    days: z
      .number({ error: "Enter a number of days" })
      .int("Whole days only")
      .min(1, "At least one day")
      .max(maxDays, `At most ${maxDays} days (the policy's exception_max_days)`),
  });
}
type ExceptionForm = z.infer<ReturnType<typeof exceptionFormSchema>>;

function ExceptionDialog({ image, onClose }: { image: Image; onClose: () => void }) {
  const { data: policy } = useImagePolicy();
  const maxDays = policy?.exception_max_days ?? FALLBACK_MAX_DAYS;
  const grant = useGrantImageException();
  const replacing = image.exception !== null;
  const form = useForm<ExceptionForm>({
    // Zod v4 inference vs zodResolver: the repo's known cast (project-conventions).
    resolver: zodResolver(exceptionFormSchema(maxDays)) as any,
    defaultValues: { reason: "", days: Math.min(DEFAULT_DAYS, maxDays) },
  });
  const days = form.watch("days");
  const errors = form.formState.errors;

  const submit = form.handleSubmit(async (values) => {
    try {
      await grant.mutateAsync({
        id: image.id,
        body: {
          reason: values.reason.trim(),
          expires_at: exceptionExpiry(values.days, new Date(), maxDays),
        },
      });
      toast.success(replacing ? "Exception replaced" : "Exception granted");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not grant the exception");
    }
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{replacing ? "Replace exception" : "Grant exception"}</DialogTitle>
          <DialogDescription>
            {`${image.reference}:${image.tag_at_add}`} is approved despite its policy reasons until
            the exception expires. It never covers a stale image, and every grant is audited.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="exception-reason">Reason</Label>
            <Textarea id="exception-reason" rows={3} {...form.register("reason")} />
            {errors.reason && <p className="text-xs text-destructive">{errors.reason.message}</p>}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="exception-days">Lasts (days)</Label>
            <Input
              id="exception-days"
              type="number"
              min={1}
              max={maxDays}
              {...form.register("days", { valueAsNumber: true })}
            />
            <p className="text-xs text-muted-foreground">
              {Number.isFinite(days) && days >= 1
                ? `Expires ${new Date(exceptionExpiry(days, new Date(), maxDays)).toLocaleDateString()}; `
                : ""}
              at most {maxDays} days.
            </p>
            {errors.days && <p className="text-xs text-destructive">{errors.days.message}</p>}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={grant.isPending}>
              Save exception
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RevokeDialog({ image, onClose }: { image: Image; onClose: () => void }) {
  const revoke = useRevokeImage();
  const users =
    image.in_use_by === 0
      ? "No process uses it."
      : `${image.in_use_by} ${image.in_use_by === 1 ? "process uses" : "processes use"} it.`;

  const confirm = async () => {
    try {
      await revoke.mutateAsync(image.id);
      toast.success("Image revoked");
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not revoke the image");
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke image</DialogTitle>
          <DialogDescription>
            Revoking is final: new deploys are refused, every run of a process whose revision
            uses {`${image.reference}:${image.tag_at_add}`} dies at launch, and any exception
            ends. {users}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={confirm} disabled={revoke.isPending}>
            Revoke
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ImageAdminActions({ image }: { image: Image }) {
  const [granting, setGranting] = useState(false);
  const [revoking, setRevoking] = useState(false);
  if (image.status === "revoked") return null;
  return (
    <section className="grid gap-2" data-testid="image-admin-actions">
      <h3 className="font-semibold">Admin</h3>
      <div className="flex flex-wrap gap-2">
        {canTakeException(image) && (
          <Button variant="outline" onClick={() => setGranting(true)}>
            <ShieldCheck className="h-4 w-4" />
            {image.exception ? "Replace exception" : "Grant exception"}
          </Button>
        )}
        <Button variant="destructive" onClick={() => setRevoking(true)}>
          <Ban className="h-4 w-4" />
          Revoke image
        </Button>
      </div>
      {granting && <ExceptionDialog image={image} onClose={() => setGranting(false)} />}
      {revoking && <RevokeDialog image={image} onClose={() => setRevoking(false)} />}
    </section>
  );
}
