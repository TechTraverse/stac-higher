import { useState } from "react";
import { useStore } from "@nanostores/react";
import {
  $catalogs,
  $activeCatalog,
  addCatalog,
  updateCatalog,
  removeCatalog,
  setActiveCatalog,
  type StacCatalog,
} from "@/stores/catalogStore";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { CatalogForm } from "./CatalogForm";
import { StacIndexImportDialog } from "./StacIndexImportDialog";
import { Button } from "@stac-higher/shared";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import { Badge } from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Plus,
  Pencil,
  Trash2,
  CheckCircle2,
  XCircle,
  Loader2,
  Globe,
  Download,
} from "lucide-react";
import { toast } from "sonner";

function CatalogCard({
  catalog,
  isActive,
  onEdit,
  onDelete,
  onSetActive,
}: {
  catalog: StacCatalog;
  isActive: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onSetActive: () => void;
}) {
  const [testing, setTesting] = useState(false);
  const [status, setStatus] = useState<"idle" | "ok" | "error">("idle");

  const testConnection = async () => {
    setTesting(true);
    setStatus("idle");
    try {
      const testUrl = catalog.url.replace(/\/+$/, "") + "/";
      const fetchUrl = catalog.proxy ? "/api/proxy" : testUrl;
      const fetchOptions: RequestInit = catalog.proxy
        ? {
            headers: {
              "X-Proxy-Target": testUrl,
              "X-Proxy-Endpoint": catalog.url,
            },
          }
        : {};
      const res = await fetch(fetchUrl, fetchOptions);
      if (res.ok) {
        setStatus("ok");
        toast.success(`Connected to ${catalog.name}`);
      } else {
        setStatus("error");
        toast.error(`Failed: ${res.status} ${res.statusText}`);
      }
    } catch (err) {
      setStatus("error");
      toast.error(`Cannot reach ${catalog.url}`);
    }
    setTesting(false);
  };

  return (
    <Card className={isActive ? "border-primary" : ""}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">{catalog.name}</CardTitle>
            {isActive && <Badge variant="default">Active</Badge>}
            {catalog.proxy && <Badge variant="secondary">Proxied</Badge>}
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={onEdit} aria-label="Edit catalog">
              <Pencil className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={onDelete} aria-label="Delete catalog">
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
          </div>
        </div>
        <CardDescription className="font-mono text-xs">
          {catalog.url}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2">
          {!isActive && (
            <Button variant="outline" size="sm" onClick={onSetActive}>
              Set Active
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={testConnection} disabled={testing}>
            {testing ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : status === "ok" ? (
              <CheckCircle2 className="h-3.5 w-3.5 mr-1.5 text-green-500" />
            ) : status === "error" ? (
              <XCircle className="h-3.5 w-3.5 mr-1.5 text-destructive" />
            ) : null}
            Test Connection
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function CatalogManagerInner() {
  const catalogs = useStore($catalogs);
  const active = useStore($activeCatalog);
  const [formOpen, setFormOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [editing, setEditing] = useState<StacCatalog | undefined>();
  const [deleteTarget, setDeleteTarget] = useState<StacCatalog | null>(null);

  const handleAdd = (data: { name: string; url: string; proxy: boolean }) => {
    addCatalog({ ...data, isDefault: catalogs.length === 0 });
    toast.success(`Added catalog: ${data.name}`);
  };

  const handleEdit = (data: { name: string; url: string; proxy: boolean }) => {
    if (!editing) return;
    updateCatalog(editing.id, data);
    toast.success(`Updated catalog: ${data.name}`);
    setEditing(undefined);
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    removeCatalog(deleteTarget.id);
    toast.success(`Removed catalog: ${deleteTarget.name}`);
    setDeleteTarget(null);
  };

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-4xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">Catalogs</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Manage your STAC API connections
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setImportOpen(true)}>
              <Download className="h-4 w-4 mr-1.5" />
              Import from StacIndex
            </Button>
            <Button onClick={() => setFormOpen(true)}>
              <Plus className="h-4 w-4 mr-1.5" />
              Add Catalog
            </Button>
          </div>
        </div>

        {catalogs.length === 0 ? (
          <Card className="border-dashed">
            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
              <Globe className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-medium mb-2">No catalogs configured</h3>
              <p className="text-sm text-muted-foreground mb-4 max-w-sm">
                Add a STAC catalog to get started browsing and managing your
                spatiotemporal data.
              </p>
              <div className="flex items-center gap-2">
                <Button variant="outline" onClick={() => setImportOpen(true)}>
                  <Download className="h-4 w-4 mr-1.5" />
                  Import from StacIndex
                </Button>
                <Button onClick={() => setFormOpen(true)}>
                  <Plus className="h-4 w-4 mr-1.5" />
                  Add Your First Catalog
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4">
            {catalogs.map((cat) => (
              <CatalogCard
                key={cat.id}
                catalog={cat}
                isActive={active?.id === cat.id}
                onEdit={() => {
                  setEditing(cat);
                }}
                onDelete={() => setDeleteTarget(cat)}
                onSetActive={() => setActiveCatalog(cat.id)}
              />
            ))}
          </div>
        )}

        <CatalogForm
          open={formOpen}
          onOpenChange={setFormOpen}
          onSubmit={handleAdd}
        />

        <StacIndexImportDialog
          open={importOpen}
          onOpenChange={setImportOpen}
        />

        {editing && (
          <CatalogForm
            open={!!editing}
            onOpenChange={(open) => !open && setEditing(undefined)}
            onSubmit={handleEdit}
            initial={editing}
          />
        )}

        <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete Catalog</DialogTitle>
              <DialogDescription>
                Are you sure you want to remove "{deleteTarget?.name}"? This action
                cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={handleDelete}>
                Delete
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </main>
    </>
  );
}

export function CatalogManagerPage() {
  return (
    <QueryProvider>
      <CatalogManagerInner />
    </QueryProvider>
  );
}
