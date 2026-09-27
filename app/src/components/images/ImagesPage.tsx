/**
 * `/images` — the platform-wide image registry (C-3, container-images spec
 * §9.2). Every scanned image is visible to every member (spec decision 7);
 * adding and rescanning are operator verbs, rendered only for them.
 * Metadata only: the platform never stores image bytes.
 */
import { useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  Label,
  LoadingState,
} from "@stac-higher/shared";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AlertTriangle, Boxes, Copy, Plus } from "lucide-react";
import { toast } from "sonner";
import { timeAgo } from "@/components/monitoring/shared";
import { useAuthMe } from "@/lib/query/auth";
import { useImages } from "@/lib/images/queries";
import { IMAGE_STATUSES, IMAGE_STATUS_LABEL, type ImageStatus } from "@/lib/images/status";
import { readVerdict } from "@/lib/images/verdict";
import { AddImageDialog } from "./AddImageDialog";
import { ImageDetailSheet } from "./ImageDetailSheet";
import { ImageStatusBadge } from "./ImageStatusBadge";
import { SeverityStack } from "./SeverityStack";
import { dbAgeDays, isExceptionExpired, shortDigest } from "./format";

function ImagesContent() {
  const { data: me } = useAuthMe();
  const roles = me?.identity?.roles ?? [];
  const canOperate = roles.includes("operator") || roles.includes("admin");

  const [status, setStatus] = useState<ImageStatus | "">("");
  const [inUse, setInUse] = useState(false);
  const [q, setQ] = useState("");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  const filtered = status !== "" || inUse || q.trim() !== "";
  const { data, isLoading, error, refetch } = useImages({
    status: status || undefined,
    q: q.trim() || undefined,
    in_use: inUse ? true : undefined,
  });
  const images = data?.images ?? [];
  const now = new Date();

  const copyDigest = async (digest: string) => {
    try {
      await navigator.clipboard.writeText(digest);
      toast.success("Digest copied");
    } catch {
      toast.error("Could not copy the digest");
    }
  };

  return (
    <div className="container mx-auto space-y-6 px-4 py-8">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Images</h1>
          <p className="text-muted-foreground">
            Container images processes may run on. Every image is scanned before it can be
            deployed, and only the scanned digest ever runs.
          </p>
        </div>
        {canOperate && (
          <Button onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" />
            Add image
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="grid gap-1.5">
          <Label htmlFor="images-search">Search images</Label>
          <Input
            id="images-search"
            placeholder="reference or tag"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-64"
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="images-status">Status</Label>
          <select
            id="images-status"
            className="flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
            value={status}
            onChange={(e) => setStatus(e.target.value as ImageStatus | "")}
          >
            <option value="">All statuses</option>
            {IMAGE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {IMAGE_STATUS_LABEL[s]}
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            aria-label="In use only"
            checked={inUse}
            onChange={(e) => setInUse(e.target.checked)}
          />
          In use only
        </label>
      </div>

      {data && data.scan_window_days === null && (
        <Card className="border-warning-border bg-warning-subtle" aria-live="polite" role="status">
          <CardContent className="flex items-center gap-3 px-5 py-3 text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
            The image policy could not be read (PROCESS_IMAGE_POLICY_FILE): staleness is unknown,
            and no image can be added or deployed until it is fixed.
          </CardContent>
        </Card>
      )}

      {isLoading && <LoadingState message="Loading images…" />}
      {error && (
        <ErrorState
          message={error instanceof Error ? error.message : "Failed to load images"}
          onRetry={() => refetch()}
        />
      )}

      {!isLoading && !error && images.length === 0 &&
        (filtered ? (
          <p className="text-sm text-muted-foreground">No image matches these filters.</p>
        ) : (
          <EmptyState
            icon={Boxes}
            title="No images yet"
            description="Add a container image to scan it. Once a scan passes, processes can run on it: as the dependency bundle for your code, or as the process itself."
            action={canOperate ? { label: "Add image", onClick: () => setAdding(true) } : undefined}
          />
        ))}

      {images.length > 0 && (
        <>
          <p className="text-sm text-muted-foreground">
            {`${images.length} ${images.length === 1 ? "image" : "images"} in the registry`}
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Image</TableHead>
                <TableHead>Digest</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Findings</TableHead>
                <TableHead>Last scanned</TableHead>
                <TableHead>In use</TableHead>
                <TableHead>Exception</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {images.map((image) => {
                const dbAge = dbAgeDays(image.db_built_at, now);
                // C-4: the word follows the DATE alone; whether new deploys
                // are refused (verdict-aware) is the detail sheet's warning.
                const exceptionExpired =
                  image.exception !== null && isExceptionExpired(image.exception.expires_at, now);
                return (
                  <TableRow key={image.id}>
                    <TableCell>
                      <button
                        type="button"
                        className="text-left font-medium hover:underline"
                        onClick={() => setSelected(image.id)}
                      >
                        {`${image.reference}:${image.tag_at_add}`}
                      </button>
                      {image.drifted && (
                        <Badge variant="outline" className="ml-2">
                          tag moved
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1">
                        <code className="tech text-xs">{shortDigest(image.digest)}</code>
                        {image.digest && (
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label="Copy digest"
                            onClick={() => copyDigest(image.digest as string)}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </span>
                    </TableCell>
                    <TableCell>
                      <ImageStatusBadge
                        status={image.status}
                        stale={image.stale === true}
                        credentialDeleted={image.registry_connection?.deleted === true}
                      />
                    </TableCell>
                    <TableCell>
                      <SeverityStack verdict={readVerdict(image.verdict)} />
                    </TableCell>
                    <TableCell className="text-sm">
                      {timeAgo(image.last_scanned_at)}
                      {dbAge !== null && (
                        <span className="block text-xs text-muted-foreground">DB {dbAge}d old</span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">{image.in_use_by}</TableCell>
                    <TableCell className="text-sm">
                      {image.exception
                        ? `${exceptionExpired ? "expired" : "until"} ${new Date(image.exception.expires_at).toLocaleDateString()}`
                        : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </>
      )}

      {adding && <AddImageDialog open onOpenChange={(open) => !open && setAdding(false)} />}
      <ImageDetailSheet imageId={selected} onClose={() => setSelected(null)} canOperate={canOperate} />
    </div>
  );
}

export function ImagesPage() {
  return (
    <AppShell>
      <ImagesContent />
    </AppShell>
  );
}
