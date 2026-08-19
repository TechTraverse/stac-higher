/**
 * Collection Settings tab (M2-E, spec §7): group ownership,
 * `externally_writable`, retention & GC knobs (readable/writable for the
 * first time since migration 003), and ADR 0009's `archived` state.
 *
 * Honesty rule: retention/GC/archived are DECLARATIVE until M2-F's jobs land
 * — the copy says so. The counted dry-run preview before a first retention
 * apply arrives with M2-F (spec §5.3), where deletion actually starts.
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
import { Archive, Save, Settings2 } from "lucide-react";
import { toast } from "sonner";
import { useAuthMe } from "@/lib/query/auth";
import {
  useCollectionSettings,
  useUpdateCollectionSettings,
} from "@/lib/collections/settings-client";

const UNOWNED = "__unowned__";

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
  const [seeded, setSeeded] = useState(false);

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

  const save = () => {
    update.mutate(
      {
        group_id: groupId,
        externally_writable: externallyWritable,
        retention_days: retentionParsed,
        gc_grace_days: graceParsed,
        archived,
      },
      {
        onSuccess: () => toast.success("Collection settings saved"),
        onError: (err) => toast.error(`Save failed: ${err.message}`),
      },
    );
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
              to this collection.
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
            Empty = keep forever. Once retention & GC lands (M2-F), items older
            than this window are deleted from the catalog and their assets
            collected after the grace period below.
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
              Mark this collection archived (ADR 0009). Enforcement — read-only
              + assets scheduled for collection — arrives with retention & GC
              (M2-F).
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

        {canAct && (
          <div>
            <Button
              data-testid="settings-save"
              disabled={update.isPending || retentionInvalid || graceInvalid}
              onClick={save}
            >
              <Save className="mr-1.5 h-4 w-4" />
              Save settings
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
