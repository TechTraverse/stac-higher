/**
 * An image's status (C-3). Labels come from `IMAGE_STATUS_LABEL`, which the
 * `image-status.json` fixture test holds to full coverage. The variant map
 * is a `Record<ImageStatus, …>`, so a new status cannot land unstyled
 * either. Stale and a deleted credential are not statuses; they are marks
 * beside one.
 */
import { Badge } from "@stac-higher/shared";
import { IMAGE_STATUS_LABEL, type ImageStatus } from "@/lib/images/status";

const VARIANT: Record<ImageStatus, "default" | "secondary" | "destructive" | "outline"> = {
  pending: "secondary",
  scanning: "secondary",
  approved: "default",
  rejected: "destructive",
  flagged: "outline",
  revoked: "destructive",
  scan_failed: "destructive",
};

export function ImageStatusBadge({
  status,
  stale = false,
  credentialDeleted = false,
}: {
  status: ImageStatus;
  stale?: boolean;
  credentialDeleted?: boolean;
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1" data-testid="image-status">
      <Badge
        variant={VARIANT[status]}
        className={status === "flagged" ? "border-warning-border text-warning" : undefined}
      >
        {IMAGE_STATUS_LABEL[status]}
      </Badge>
      {stale && (
        <Badge variant="outline" className="border-warning-border text-warning">
          Stale
        </Badge>
      )}
      {credentialDeleted && <Badge variant="destructive">Credential deleted</Badge>}
    </span>
  );
}
