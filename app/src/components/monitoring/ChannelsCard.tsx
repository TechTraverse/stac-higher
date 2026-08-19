/**
 * /monitoring notification-channel management (M2-C surface, M2-D UI).
 * Lists the caller's groups' channels; operators add webhook / in-app
 * channels and remove them. The webhook secret is write-only — the list
 * shows `signed` when one is set, never the value.
 */
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Input,
  Label,
  LoadingState,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { BellRing, KeyRound, Plus, Trash2, Webhook } from "lucide-react";
import { toast } from "sonner";
import type { Channel } from "@/lib/monitoring/api";
import {
  useChannels,
  useCreateChannel,
  useDeleteChannel,
} from "@/lib/monitoring/queries";

function ChannelRow({
  channel,
  canAct,
}: {
  channel: Channel;
  canAct: boolean;
}) {
  const del = useDeleteChannel();
  return (
    <div
      data-testid={`channel-row-${channel.id}`}
      className="flex items-center justify-between gap-3 rounded-md border border-border p-3"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="secondary">
            {channel.kind === "webhook" ? (
              <Webhook className="mr-1 h-3 w-3" />
            ) : (
              <BellRing className="mr-1 h-3 w-3" />
            )}
            {channel.kind}
          </Badge>
          <span className="text-xs text-muted-foreground">
            group {channel.group_id}
          </span>
          {channel.config.has_secret && (
            <Badge variant="outline" className="text-[10px]">
              <KeyRound className="mr-0.5 h-2.5 w-2.5" />
              signed
            </Badge>
          )}
        </div>
        {channel.kind === "webhook" && (
          <p className="mt-1 break-all text-xs text-muted-foreground">
            {channel.config.url}
          </p>
        )}
      </div>
      {canAct && (
        <Button
          variant="ghost"
          size="sm"
          className="shrink-0 text-destructive"
          disabled={del.isPending}
          aria-label="Remove channel"
          onClick={() =>
            del.mutate(channel.id, {
              onSuccess: () => toast.success("Channel removed"),
              onError: (err) => toast.error(`Remove failed: ${err.message}`),
            })
          }
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      )}
    </div>
  );
}

function AddChannelDialog({
  open,
  onOpenChange,
  groups,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  groups: string[];
}) {
  const create = useCreateChannel();
  const [kind, setKind] = useState<"webhook" | "in_app">("webhook");
  const [group, setGroup] = useState(groups[0] ?? "");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");

  const submit = () => {
    create.mutate(
      {
        kind,
        group_id: group,
        ...(kind === "webhook"
          ? { config: { url, ...(secret ? { secret } : {}) } }
          : {}),
      },
      {
        onSuccess: () => {
          toast.success("Channel added");
          onOpenChange(false);
          setUrl("");
          setSecret("");
        },
        onError: (err) => toast.error(`Add failed: ${err.message}`),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add notification channel</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label>Kind</Label>
            <Select
              value={kind}
              onValueChange={(value) => setKind(value as "webhook" | "in_app")}
            >
              <SelectTrigger data-testid="channel-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="webhook">Webhook</SelectItem>
                <SelectItem value="in_app">In-app</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label>Group</Label>
            {groups.length > 0 ? (
              <Select value={group} onValueChange={setGroup}>
                <SelectTrigger data-testid="channel-group">
                  <SelectValue placeholder="Select a group" />
                </SelectTrigger>
                <SelectContent>
                  {groups.map((g) => (
                    <SelectItem key={g} value={g}>
                      {g}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                value={group}
                onChange={(e) => setGroup(e.target.value)}
                placeholder="group id"
              />
            )}
          </div>
          {kind === "webhook" && (
            <>
              <div className="grid gap-1.5">
                <Label htmlFor="channel-url">URL</Label>
                <Input
                  id="channel-url"
                  data-testid="channel-url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://hooks.example.com/stac"
                />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="channel-secret">
                  Signing secret{" "}
                  <span className="text-muted-foreground">(optional)</span>
                </Label>
                <Input
                  id="channel-secret"
                  type="password"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder="HMAC-SHA256 shared secret"
                />
              </div>
            </>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            data-testid="channel-submit"
            disabled={
              create.isPending || !group || (kind === "webhook" && !url)
            }
            onClick={submit}
          >
            Add channel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ChannelsCard({
  canAct,
  groups,
}: {
  canAct: boolean;
  groups: string[];
}) {
  const { data: channels, isLoading, isError, error, refetch } = useChannels();
  const [addOpen, setAddOpen] = useState(false);

  return (
    <Card data-testid="channels-card">
      <CardHeader className="flex flex-row items-center justify-between pb-3">
        <CardTitle className="flex items-center gap-2 text-lg">
          <BellRing className="h-5 w-5" />
          Notification channels
        </CardTitle>
        {canAct && (
          <Button size="sm" data-testid="channel-add" onClick={() => setAddOpen(true)}>
            <Plus className="mr-1.5 h-4 w-4" />
            Add channel
          </Button>
        )}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <LoadingState message="Loading channels…" />
        ) : isError ? (
          <ErrorState
            message={
              error instanceof Error ? error.message : "Failed to load channels"
            }
            onRetry={() => refetch()}
          />
        ) : !channels || channels.length === 0 ? (
          <EmptyState
            icon={BellRing}
            title="No notification channels"
            description="Alerts stay visible here and on the bell either way; add a webhook channel to push them to an external system."
          />
        ) : (
          <div className="grid gap-2">
            {channels.map((channel) => (
              <ChannelRow key={channel.id} channel={channel} canAct={canAct} />
            ))}
          </div>
        )}
        {addOpen && (
          <AddChannelDialog
            open={addOpen}
            onOpenChange={setAddOpen}
            groups={groups}
          />
        )}
      </CardContent>
    </Card>
  );
}
