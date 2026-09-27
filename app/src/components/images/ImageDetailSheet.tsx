/**
 * One image, in a sheet over the dashboard (C-3, container-images spec
 * §9.2): what would stop a deploy (credential deleted, stale, the config
 * warning, the policy reasons verbatim), the top findings (KEV first, then
 * risk), the scan history, who uses it, and "Rescan now" for operators.
 * Scan-history diffs and the admin exception form are C-4; a live exception
 * is shown read-only here.
 */
import { Badge, Button, LoadingState } from "@stac-higher/shared";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { timeAgo } from "@/components/monitoring/shared";
import { useImage, useRescanImage } from "@/lib/images/queries";
import { exceptionLapsed, readVerdict } from "@/lib/images/verdict";
import { ImageStatusBadge } from "./ImageStatusBadge";
import { SeverityStack } from "./SeverityStack";
import { configWarning, latestScanResult, scanError, shortDigest, sortFindings } from "./format";

const TOP_FINDINGS = 10;

export function ImageDetailSheet({
  imageId,
  onClose,
  canOperate,
}: {
  imageId: string | null;
  onClose: () => void;
  canOperate: boolean;
}) {
  const { data, isLoading, error } = useImage(imageId);
  const rescan = useRescanImage();
  const image = data?.image ?? null;
  const verdict = readVerdict(image?.verdict);
  const latest = latestScanResult(data?.scans ?? [], image?.last_scan_id ?? null);
  const findings = latest.status === "ok" ? sortFindings(latest.result.top ?? []).slice(0, TOP_FINDINGS) : [];
  const warning = configWarning(image?.config);
  const now = new Date();
  const lapsed =
    image !== null &&
    exceptionLapsed(
      { status: image.status, exceptionExpiresAt: image.exception?.expires_at ?? null, verdict: image.verdict },
      now,
    );

  const requestRescan = async () => {
    if (!image) return;
    try {
      const requested = await rescan.mutateAsync(image.id);
      toast.success(
        requested.kind === "admission" ? "Scan re-requested" : "Rescan requested",
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not request a rescan");
    }
  };

  return (
    <Sheet open={imageId !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle className="break-all">
            {image ? `${image.reference}:${image.tag_at_add}` : "Image"}
          </SheetTitle>
          <SheetDescription className="tech break-all">
            {image?.digest ?? "The digest resolves when the scan runs"}
          </SheetDescription>
        </SheetHeader>

        {isLoading && <LoadingState message="Loading image…" />}
        {error && (
          <p className="px-4 text-sm text-destructive">
            {error instanceof Error ? error.message : "Could not load the image"}
          </p>
        )}

        {image && data && (
          <div className="grid gap-5 px-4 pb-6 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <ImageStatusBadge
                status={image.status}
                stale={image.stale === true}
                credentialDeleted={image.registry_connection?.deleted === true}
              />
              <SeverityStack verdict={verdict} />
            </div>

            {image.registry_connection?.deleted && (
              <p className="text-destructive" data-testid="credential-deleted">
                Pulled with the registry credential &quot;{image.registry_connection.name}&quot;,
                which was deleted. No process can deploy this image until it is added again
                with a live credential.
              </p>
            )}
            {image.stale && (
              <p className="text-warning">
                Last scanned {timeAgo(image.last_scanned_at)}, outside the scan window: new
                deploys and launches are refused until a rescan passes.
              </p>
            )}
            {image.drifted && (
              <p>
                Tag <code className="tech">{image.tag_at_add}</code> now points to{" "}
                <code className="tech">{shortDigest(image.tag_current_digest)}</code>. Runs keep
                using the scanned digest; add the reference again to scan the new one.
              </p>
            )}
            {warning && (
              <p className="text-warning" data-testid="config-warning">
                {warning}
              </p>
            )}
            {image.status === "scan_failed" && (
              <p className="text-destructive">
                The last scan failed: {scanError(data.scans[0]) ?? "no message recorded"}.
              </p>
            )}

            {image.exception && (
              <section className="grid gap-1">
                <h3 className="font-semibold">Exception</h3>
                <p>{image.exception.reason}</p>
                <p className="text-muted-foreground">
                  Granted by {image.exception.by},{" "}
                  {lapsed ? "expired" : "expires"}{" "}
                  {new Date(image.exception.expires_at).toLocaleDateString()}
                </p>
                {lapsed && (
                  <p className="text-warning">
                    The exception expired and the latest scan still fails the policy: new deploys
                    are refused until a rescan passes.
                  </p>
                )}
              </section>
            )}

            {verdict && verdict.reasons.length > 0 && (
              <section className="grid gap-1">
                <h3 className="font-semibold">Policy reasons</h3>
                <ul className="list-disc pl-5">
                  {verdict.reasons.map((reason) => (
                    <li key={reason}>
                      <code className="tech">{reason}</code>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {latest.status === "unreadable" && (
              <p className="text-warning">Findings for the latest scan could not be read</p>
            )}

            {findings.length > 0 && (
              <section className="grid gap-1">
                <h3 className="font-semibold">Top findings</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Vulnerability</TableHead>
                      <TableHead>Package</TableHead>
                      <TableHead>Fixed in</TableHead>
                      <TableHead>Risk</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {findings.map((finding) => (
                      <TableRow key={`${finding.id}:${finding.package}`}>
                        <TableCell>
                          <span className="tech">{finding.id}</span>{" "}
                          {finding.kev && <Badge variant="destructive">KEV</Badge>}
                          <span className="block text-xs text-muted-foreground">{finding.severity}</span>
                        </TableCell>
                        <TableCell className="tech">{`${finding.package}@${finding.version}`}</TableCell>
                        <TableCell className="tech">{finding.fixed_in ?? "no fix"}</TableCell>
                        <TableCell className="tabular-nums">{finding.risk.toFixed(2)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </section>
            )}

            <section className="grid gap-1">
              <h3 className="font-semibold">Scan history</h3>
              {data.scans.length === 0 ? (
                <p className="text-muted-foreground">No scans yet.</p>
              ) : (
                <ul className="grid gap-1">
                  {data.scans.map((s) => (
                    <li key={s.id} className="flex flex-wrap items-center gap-2">
                      <Badge variant={s.status === "failed" ? "destructive" : "secondary"}>
                        {s.status}
                      </Badge>
                      <span>{s.kind}</span>
                      <span className="text-muted-foreground">
                        requested {timeAgo(s.requested_at)}
                        {s.finished_at ? `, finished ${timeAgo(s.finished_at)}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="grid gap-1">
              <h3 className="font-semibold">In use by</h3>
              {data.in_use_by.length === 0 && data.in_use_elsewhere === 0 ? (
                <p className="text-muted-foreground">No process&apos;s current revision uses it.</p>
              ) : (
                <ul className="grid gap-1">
                  {data.in_use_by.map((user) => (
                    <li key={user.process_id}>
                      <a className="text-primary hover:underline" href={`/processes/${user.process_id}`}>
                        {user.name}
                      </a>{" "}
                      <span className="text-muted-foreground">({user.group_id})</span>
                    </li>
                  ))}
                  {data.in_use_elsewhere > 0 && (
                    <li className="text-muted-foreground">
                      {data.in_use_elsewhere} more in other groups
                    </li>
                  )}
                </ul>
              )}
            </section>

            {canOperate && image.status !== "revoked" && (
              <div aria-live="polite" role="status">
                <Button onClick={requestRescan} disabled={rescan.isPending}>
                  {rescan.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  Rescan now
                </Button>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
