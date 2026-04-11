import { useState } from "react";
import type { StacAsset, StacCollection } from "@/lib/stac-api/types";
import { useUpdateCollection } from "@/lib/query/collections";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Plus, Pencil, Trash2, ExternalLink } from "lucide-react";
import { toast } from "sonner";

interface AssetFormState {
  key: string;
  href: string;
  type: string;
  title: string;
  description: string;
  roles: string;
}

const emptyForm: AssetFormState = {
  key: "",
  href: "",
  type: "",
  title: "",
  description: "",
  roles: "",
};

function AssetFormDialog({
  open,
  onOpenChange,
  onSubmit,
  initial,
  editingKey,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (key: string, asset: StacAsset) => void;
  initial?: AssetFormState;
  editingKey?: string;
}) {
  const [form, setForm] = useState<AssetFormState>(initial ?? emptyForm);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.key.trim() || !form.href.trim()) return;
    const asset: StacAsset = {
      href: form.href.trim(),
      type: form.type.trim() || undefined,
      title: form.title.trim() || undefined,
      description: form.description.trim() || undefined,
      roles: form.roles.trim()
        ? form.roles.split(",").map((r) => r.trim()).filter(Boolean)
        : undefined,
    };
    onSubmit(form.key.trim(), asset);
    if (!editingKey) setForm(emptyForm);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editingKey ? "Edit Asset" : "Add Asset"}</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">Key</Label>
            <Input
              value={form.key}
              onChange={(e) => setForm({ ...form, key: e.target.value })}
              placeholder="thumbnail"
              disabled={!!editingKey}
              required
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">URL</Label>
            <Input
              value={form.href}
              onChange={(e) => setForm({ ...form, href: e.target.value })}
              placeholder="https://..."
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label className="text-xs">Title</Label>
              <Input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="Asset title"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Media Type</Label>
              <Input
                value={form.type}
                onChange={(e) => setForm({ ...form, type: e.target.value })}
                placeholder="image/png"
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Description</Label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="Optional description"
              rows={2}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Roles (comma-separated)</Label>
            <Input
              value={form.roles}
              onChange={(e) => setForm({ ...form, roles: e.target.value })}
              placeholder="data, thumbnail"
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit">{editingKey ? "Save" : "Add"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

interface AssetManagerProps {
  collection: StacCollection;
  endpointUrl: string;
}

export function AssetManager({ collection, endpointUrl }: AssetManagerProps) {
  const updateMutation = useUpdateCollection(endpointUrl);
  const [addOpen, setAddOpen] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);

  const assets = collection.assets ?? {};

  const saveAssets = (newAssets: Record<string, StacAsset>) => {
    updateMutation.mutate(
      {
        collectionId: collection.id,
        data: { ...collection, assets: newAssets },
      },
      {
        onSuccess: () => toast.success("Assets updated"),
        onError: (err) => toast.error(`Failed to update: ${err.message}`),
      },
    );
  };

  const handleAdd = (key: string, asset: StacAsset) => {
    saveAssets({ ...assets, [key]: asset });
  };

  const handleEdit = (key: string, asset: StacAsset) => {
    saveAssets({ ...assets, [key]: asset });
    setEditingKey(null);
  };

  const handleDelete = (key: string) => {
    const next = { ...assets };
    delete next[key];
    saveAssets(next);
  };

  const editingAsset = editingKey ? assets[editingKey] : undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Collection Assets</h2>
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4 mr-1.5" />
          Add Asset
        </Button>
      </div>

      {Object.keys(assets).length === 0 ? (
        <p className="text-sm text-muted-foreground py-8 text-center">
          No collection-level assets yet.
        </p>
      ) : (
        <div className="grid gap-3">
          {Object.entries(assets).map(([key, asset]) => (
            <Card key={key}>
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-sm">{asset.title || key}</CardTitle>
                  <div className="flex items-center gap-1">
                    {asset.roles?.map((role) => (
                      <Badge key={role} variant="outline" className="text-xs">
                        {role}
                      </Badge>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="flex items-center justify-between">
                  <div className="min-w-0">
                    <p className="text-xs text-muted-foreground font-mono truncate">{key}</p>
                    {asset.type && (
                      <p className="text-xs text-muted-foreground">{asset.type}</p>
                    )}
                    {asset.description && (
                      <p className="text-xs text-muted-foreground mt-1">{asset.description}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0 ml-2">
                    <a href={asset.href} target="_blank" rel="noopener noreferrer">
                      <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Open asset URL">
                        <ExternalLink className="h-3.5 w-3.5" />
                      </Button>
                    </a>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => setEditingKey(key)}
                      aria-label="Edit asset"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => handleDelete(key)}
                      aria-label="Delete asset"
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <AssetFormDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        onSubmit={handleAdd}
      />

      {editingKey && editingAsset && (
        <AssetFormDialog
          open={!!editingKey}
          onOpenChange={(open) => { if (!open) setEditingKey(null); }}
          onSubmit={handleEdit}
          editingKey={editingKey}
          initial={{
            key: editingKey,
            href: editingAsset.href,
            type: editingAsset.type ?? "",
            title: editingAsset.title ?? "",
            description: editingAsset.description ?? "",
            roles: editingAsset.roles?.join(", ") ?? "",
          }}
        />
      )}
    </div>
  );
}
