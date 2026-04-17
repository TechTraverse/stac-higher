import { useState } from "react";
import { Button } from "@stac-higher/shared";
import { Input } from "@stac-higher/shared";
import { Label } from "@stac-higher/shared";
import { Switch } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import type { StacCatalog } from "@/stores/catalogStore";

interface CatalogFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (data: { name: string; url: string; proxy: boolean }) => void;
  initial?: StacCatalog;
}

export function CatalogForm({
  open,
  onOpenChange,
  onSubmit,
  initial,
}: CatalogFormProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [proxy, setProxy] = useState(initial?.proxy ?? false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !url.trim()) return;
    onSubmit({ name: name.trim(), url: url.trim().replace(/\/+$/, ""), proxy });
    if (!initial) {
      setName("");
      setUrl("");
      setProxy(false);
    }
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {initial ? "Edit Catalog" : "Add STAC Catalog"}
          </DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="cat-name">Name</Label>
            <Input
              id="cat-name"
              placeholder="My STAC API"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="cat-url">URL</Label>
            <Input
              id="cat-url"
              placeholder="http://localhost:8082"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              type="url"
            />
          </div>
          <div className="flex items-center justify-between rounded-md border p-3">
            <div className="space-y-0.5">
              <Label htmlFor="cat-proxy" className="text-sm font-medium">
                Proxy through server
              </Label>
              <p className="text-xs text-muted-foreground">
                Route requests through the server to bypass CORS restrictions
              </p>
            </div>
            <Switch
              id="cat-proxy"
              checked={proxy}
              onCheckedChange={setProxy}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit">{initial ? "Save" : "Add"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
