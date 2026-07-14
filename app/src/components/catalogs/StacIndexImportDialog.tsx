import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useStore } from "@nanostores/react";
import {
  Button,
  Input,
  Label,
  Switch,
  Badge,
  LoadingState,
  ErrorState,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { $catalogs, addCatalog } from "@/stores/catalogStore";
import { toast } from "sonner";

const STACINDEX_URL = "https://stacindex.org/api/catalogs";

interface StacIndexEntry {
  id: number;
  title: string;
  url: string;
  summary?: string;
  isApi?: boolean;
}

function normalizeUrl(u: string) {
  return u.replace(/\/+$/, "").toLowerCase();
}

async function fetchStacIndexApis(): Promise<StacIndexEntry[]> {
  const res = await fetch("/api/proxy", {
    headers: {
      "X-Proxy-Target": STACINDEX_URL,
      "X-Proxy-Endpoint": STACINDEX_URL,
      Accept: "application/json",
    },
  });
  if (!res.ok) {
    throw new Error(`StacIndex fetch failed: ${res.status} ${res.statusText}`);
  }
  const data = (await res.json()) as StacIndexEntry[];
  return data
    .filter((c) => c.isApi && c.url)
    .sort((a, b) => a.title.localeCompare(b.title));
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function StacIndexImportDialog({ open, onOpenChange }: Props) {
  const catalogs = useStore($catalogs);
  const existingUrls = useMemo(
    () => new Set(catalogs.map((c) => normalizeUrl(c.url))),
    [catalogs],
  );

  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [proxyDefault, setProxyDefault] = useState(true);
  const [filter, setFilter] = useState("");

  const query = useQuery({
    queryKey: ["stacindex", "catalogs"],
    queryFn: fetchStacIndexApis,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });

  const entries = query.data ?? [];
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (e) =>
        e.title.toLowerCase().includes(q) ||
        e.url.toLowerCase().includes(q) ||
        e.summary?.toLowerCase().includes(q),
    );
  }, [entries, filter]);

  const selectableFiltered = filtered.filter(
    (e) => !existingUrls.has(normalizeUrl(e.url)),
  );
  const allSelected =
    selectableFiltered.length > 0 &&
    selectableFiltered.every((e) => selected.has(e.id));

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    setSelected((prev) => {
      if (allSelected) {
        const next = new Set(prev);
        for (const e of selectableFiltered) next.delete(e.id);
        return next;
      }
      const next = new Set(prev);
      for (const e of selectableFiltered) next.add(e.id);
      return next;
    });
  };

  const handleImport = () => {
    const chosen = entries.filter((e) => selected.has(e.id));
    let added = 0;
    for (const entry of chosen) {
      if (existingUrls.has(normalizeUrl(entry.url))) continue;
      addCatalog({
        name: entry.title,
        url: entry.url.replace(/\/+$/, ""),
        isDefault: false,
        proxy: proxyDefault,
      });
      added++;
    }
    toast.success(
      added === 1 ? "Imported 1 catalog" : `Imported ${added} catalogs`,
    );
    setSelected(new Set());
    onOpenChange(false);
  };

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setSelected(new Set());
      setFilter("");
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Import from StacIndex</DialogTitle>
          <DialogDescription>
            Pick STAC APIs from{" "}
            <a
              href="https://stacindex.org/catalogs?type=api"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              stacindex.org
            </a>
            . Already-imported catalogs are disabled.
          </DialogDescription>
        </DialogHeader>

        {query.isLoading ? (
          <LoadingState message="Loading StacIndex…" />
        ) : query.isError ? (
          <ErrorState
            title="Could not load StacIndex"
            message={(query.error as Error)?.message ?? "Unknown error"}
            onRetry={() => query.refetch()}
          />
        ) : (
          <>
            <div className="flex items-center gap-2">
              <Input
                placeholder="Filter by name, URL, or description…"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                className="flex-1"
              />
              <Button
                variant="outline"
                size="sm"
                onClick={toggleAll}
                disabled={selectableFiltered.length === 0}
              >
                {allSelected ? "Clear" : "Select all"}
              </Button>
            </div>

            <div className="flex-1 overflow-y-auto border rounded-md divide-y">
              {filtered.length === 0 ? (
                <p className="p-4 text-sm text-muted-foreground text-center">
                  No catalogs match the filter.
                </p>
              ) : (
                filtered.map((entry) => {
                  const already = existingUrls.has(normalizeUrl(entry.url));
                  const checked = selected.has(entry.id);
                  return (
                    <label
                      key={entry.id}
                      className={`flex items-start gap-3 p-3 cursor-pointer hover:bg-accent/40 ${
                        already ? "opacity-50 cursor-not-allowed" : ""
                      }`}
                    >
                      <input
                        type="checkbox"
                        className="mt-1 h-4 w-4 accent-primary"
                        checked={already || checked}
                        disabled={already}
                        onChange={() => !already && toggle(entry.id)}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-medium text-sm">
                            {entry.title}
                          </span>
                          {already && (
                            <Badge variant="secondary" className="text-xs">
                              Already imported
                            </Badge>
                          )}
                        </div>
                        <div className="font-mono text-xs text-muted-foreground truncate">
                          {entry.url}
                        </div>
                        {entry.summary && (
                          <div className="text-xs text-muted-foreground mt-1">
                            {entry.summary}
                          </div>
                        )}
                      </div>
                    </label>
                  );
                })
              )}
            </div>

            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="space-y-0.5">
                <Label htmlFor="import-proxy" className="text-sm font-medium">
                  Proxy through server
                </Label>
                <p className="text-xs text-muted-foreground">
                  Recommended — most public STAC APIs lack permissive CORS.
                </p>
              </div>
              <Switch
                id="import-proxy"
                checked={proxyDefault}
                onCheckedChange={setProxyDefault}
              />
            </div>
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleImport} disabled={selected.size === 0}>
            Import {selected.size > 0 ? `(${selected.size})` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
