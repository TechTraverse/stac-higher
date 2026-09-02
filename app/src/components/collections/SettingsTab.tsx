/**
 * Collection Settings tab (M2-E, spec §7): group ownership,
 * `externally_writable`, retention & GC knobs (enforced by M2-F's sweeps,
 * ADR 0011), ADR 0009's `archived` state, and the link-level OGC serving
 * toggle (titiler-pgstac / tipg — docs/serving.md; effectively public until
 * I-1, the copy says so). Deletion-starting saves go through the counted
 * dry-run warn-and-proceed dialog (M2-F, spec §5.3).
 */
import { useEffect, useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  Input,
  Label,
  LoadingState,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Archive,
  ExternalLink,
  Globe,
  Save,
  Settings2,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";
import { useAuthMe } from "@/lib/query/auth";
import {
  useCollectionSettings,
  useUpdateCollectionSettings,
} from "@/lib/collections/settings-client";
import { collectionInfoUrl } from "@/lib/serving/urls";

const UNOWNED = "__unowned__";

// Local OGC serving services (docker compose; docs/serving.md). Link-level
// exposure only — the toggle controls whether this page advertises them.
// The titiler base + URL shapes come from lib/serving/urls so this page, the
// product overview and the item preview cannot drift apart.
const TIPG_URL = import.meta.env.PUBLIC_TIPG_URL ?? "http://localhost:8085";

export function SettingsTab({ collectionId }: { collectionId: string }) {
  const { data: auth } = useAuthMe();
  const identity = auth?.authenticated ? auth.identity : null;
  const isAdmin = identity?.roles.includes("admin") ?? false;
  const canAct =
    identity?.roles.some((role) => role === "operator" || role === "admin") ??
    false;
  const groups = identity?.groups ?? [];

  const { data: settings, isLoading, isError, error, refetch } =
    useCollectionSettings(collectionId);
  const update = useUpdateCollectionSettings(collectionId);

  const [groupId, setGroupId] = useState<string | null>(null);
  const [externallyWritable, setExternallyWritable] = useState(false);
  const [retentionDays, setRetentionDays] = useState<string>("");
  const [gcGraceDays, setGcGraceDays] = useState<string>("30");
  const [archived, setArchived] = useState(false);
  const [servingEnabled, setServingEnabled] = useState(false);
  const [seeded, setSeeded] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [confirm, setConfirm] = useState<{
    archiving: boolean;
    impact: { total_items: number | null; expired_items: number | null };
  } | null>(null);

  // Seed the form once from the loaded settings (refetches don't clobber
  // in-progress edits; a save invalidates and we keep the saved values).
  useEffect(() => {
    if (settings && !seeded) {
      setGroupId(settings.groupId);
      setExternallyWritable(settings.externallyWritable);
      setRetentionDays(
        settings.retentionDays === null ? "" : String(settings.retentionDays),
      );
      setGcGraceDays(String(settings.gcGraceDays));
      setArchived(settings.archived);
      setServingEnabled(settings.servingEnabled);
      setSeeded(true);
    }
  }, [settings, seeded]);

  if (isLoading || !seeded) {
    return isError ? (
      <ErrorState
        message={error instanceof Error ? error.message : "Failed to load settings"}
        onRetry={() => refetch()}
      />
    ) : (
      <LoadingState message="Loading settings…" />
    );
  }

  const retentionParsed =
    retentionDays.trim() === "" ? null : Number(retentionDays);
  const graceParsed = Number(gcGraceDays);
  const retentionInvalid =
    retentionParsed !== null &&
    (!Number.isInteger(retentionParsed) || retentionParsed < 1);
  const graceInvalid = !Number.isInteger(graceParsed) || graceParsed < 0;

  // The current owner group may be outside the caller's groups (admin case);
  // keep it selectable so an admin save doesn't silently drop ownership.
  const groupOptions = Array.from(
    new Set([...groups, ...(settings?.groupId ? [settings.groupId] : [])]),
  );

  const doSave = () => {
    setConfirm(null);
    update.mutate(
      {
        group_id: groupId,
        externally_writable: externallyWritable,
        retention_days: retentionParsed,
        gc_grace_days: graceParsed,
        archived,
        serving_enabled: servingEnabled,
      },
      {
        onSuccess: () => toast.success("Collection settings saved"),
        onError: (err) => toast.error(`Save failed: ${err.message}`),
      },
    );
  };

  // Warn-and-proceed (M2-F, spec §5.3 / ADR 0009 §6): saving a change that
  // starts deleting data — enabling/tightening retention, or archiving —
  // first shows the counted dry-run so the operator sees what the sweep will
  // expire before it exists.
  const save = async () => {
    const archiving = archived && !settings?.archived;
    const retentionTightened =
      retentionParsed !== null &&
      (settings?.retentionDays === null ||
        settings === undefined ||
        retentionParsed < (settings?.retentionDays ?? Infinity));
    if (!archiving && !retentionTightened) {
      doSave();
      return;
    }
    setPreviewLoading(true);
    try {
      const params = new URLSearchParams();
      if (archiving) params.set("archived", "true");
      else if (retentionParsed !== null)
        params.set("retention_days", String(retentionParsed));
      const res = await fetch(
        `/api/collections/${encodeURIComponent(collectionId)}/settings/impact?${params}`,
        { credentials: "same-origin" },
      );
      const impact = res.ok
        ? ((await res.json()) as {
            total_items: number | null;
            expired_items: number | null;
          })
        : { total_items: null, expired_items: null };
      setConfirm({ archiving, impact });
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <Card data-testid="settings-tab">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <Settings2 className="h-5 w-5" />
          Platform settings
          {archived && (
            <Badge variant="outline">
              <Archive className="mr-1 h-3 w-3" />
              archived
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid max-w-xl gap-5">
        <div className="grid gap-1.5">
          <Label>Owning group</Label>
          <Select
            value={groupId ?? UNOWNED}
            onValueChange={(value) =>
              setGroupId(value === UNOWNED ? null : value)
            }
            disabled={!canAct}
          >
            <SelectTrigger data-testid="settings-group">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={UNOWNED}>Unowned (public)</SelectItem>
              {groupOptions.map((g) => (
                <SelectItem key={g} value={g}>
                  {g}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            Unowned collections are visible to everyone and manageable by any
            operator (ADR 0003).{" "}
            {!isAdmin && "Transfers must target one of your groups."}
          </p>
        </div>

        <div className="flex items-center justify-between gap-3">
          <div>
            <Label htmlFor="settings-writable">Externally writable</Label>
            <p className="text-xs text-muted-foreground">
              Allow external clients (direct bearer auth at the proxy) to write
              to this product.
            </p>
          </div>
          <Switch
            id="settings-writable"
            data-testid="settings-writable"
            checked={externallyWritable}
            onCheckedChange={setExternallyWritable}
            disabled={!canAct}
          />
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="settings-retention">Retention (days)</Label>
          <Input
            id="settings-retention"
            data-testid="settings-retention"
            inputMode="numeric"
            placeholder="keep forever"
            value={retentionDays}
            onChange={(e) => setRetentionDays(e.target.value)}
            disabled={!canAct}
          />
          <p className="text-xs text-muted-foreground">
            Empty = keep forever. Items older than this window are deleted
            from the catalog by the retention sweep and their assets collected
            after the grace period below (M2-F, ADR 0011).
          </p>
          {retentionInvalid && (
            <p className="text-xs text-destructive">
              Retention must be a whole number of days (≥ 1) or empty.
            </p>
          )}
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="settings-grace">GC grace (days)</Label>
          <Input
            id="settings-grace"
            data-testid="settings-grace"
            inputMode="numeric"
            value={gcGraceDays}
            onChange={(e) => setGcGraceDays(e.target.value)}
            disabled={!canAct}
          />
          <p className="text-xs text-muted-foreground">
            How long expired assets stay recoverable in object storage before
            permanent deletion (default 30).
          </p>
          {graceInvalid && (
            <p className="text-xs text-destructive">
              Grace must be a whole number of days (≥ 0).
            </p>
          )}
        </div>

        <div className="flex items-center justify-between gap-3">
          <div>
            <Label htmlFor="settings-archived">Archived</Label>
            <p className="text-xs text-muted-foreground">
              Mark this product archived (ADR 0009): item writes and new
              associations are refused, and the retention sweep expires every
              item (assets collected after the grace window).
            </p>
          </div>
          <Switch
            id="settings-archived"
            data-testid="settings-archived"
            checked={archived}
            onCheckedChange={setArchived}
            disabled={!canAct}
          />
        </div>

        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <Label htmlFor="settings-serving" className="flex items-center gap-1.5">
                <Globe className="h-4 w-4" />
                OGC serving
              </Label>
              <p className="text-xs text-muted-foreground">
                Advertise this product's OGC API endpoints (raster tiles via
                titiler-pgstac; vector features/tiles via tipg). Link-level
                only: until per-collection read visibility lands (I-1), the
                serving services are effectively public — enable this only for
                collections whose data may be public.
              </p>
            </div>
            <Switch
              id="settings-serving"
              data-testid="settings-serving"
              checked={servingEnabled}
              onCheckedChange={setServingEnabled}
              disabled={!canAct}
            />
          </div>
          {servingEnabled && (
            <div
              className="grid gap-1 rounded-md border p-3 text-sm"
              data-testid="settings-serving-links"
            >
              <a
                className="flex items-center gap-1.5 text-primary hover:underline"
                href={collectionInfoUrl(collectionId)}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Raster tiles — titiler-pgstac collection endpoints
              </a>
              <a
                className="flex items-center gap-1.5 text-primary hover:underline"
                href={`${TIPG_URL}/`}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Vector features &amp; tiles — tipg (stack-wide OGC API landing
                page; tipg serves database tables, not STAC collections)
              </a>
              <p className="text-xs text-muted-foreground">
                Raster tiling reads asset hrefs from item JSON — platform
                (`/api/assets/…`) hrefs are app-relative and not resolvable by
                the tiler; reference-mode items with absolute URLs serve
                directly (docs/serving.md).
              </p>
            </div>
          )}
        </div>

        {canAct && (
          <div>
            <Button
              data-testid="settings-save"
              disabled={
                update.isPending ||
                previewLoading ||
                retentionInvalid ||
                graceInvalid
              }
              onClick={() => void save()}
            >
              <Save className="mr-1.5 h-4 w-4" />
              Save settings
            </Button>
          </div>
        )}

        <Dialog
          open={confirm !== null}
          onOpenChange={(open) => !open && setConfirm(null)}
        >
          <DialogContent className="sm:max-w-md">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <TriangleAlert className="h-5 w-5 text-destructive" />
                {confirm?.archiving
                  ? "Archive this product?"
                  : "Enable retention?"}
              </DialogTitle>
              <DialogDescription data-testid="settings-impact">
                {confirm?.archiving
                  ? confirm.impact.total_items === null
                    ? "Every item in this product will be deleted (count unavailable)."
                    : `All ${confirm.impact.total_items} items in this product will be deleted from the catalog.`
                  : confirm?.impact.expired_items === null
                    ? `Items older than ${retentionParsed} days will be deleted (count unavailable).`
                    : `${confirm?.impact.expired_items} of ${confirm?.impact.total_items} items are already older than ${retentionParsed} days and will be deleted by the first sweep.`}{" "}
                Their asset files leave object storage after the{" "}
                {graceParsed}-day grace window. This is ADR 0009
                warn-and-proceed: it does exactly what it says.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirm(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                data-testid="settings-confirm"
                onClick={doSave}
              >
                {confirm?.archiving ? "Archive product" : "Enable retention"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
