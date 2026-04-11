import { useState } from "react";
import { useStore } from "@nanostores/react";
import {
  $endpoints,
  $activeEndpoint,
  addEndpoint,
  updateEndpoint,
  removeEndpoint,
  setActiveEndpoint,
  type StacEndpoint,
} from "@/stores/endpointStore";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { EndpointForm } from "./EndpointForm";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
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
} from "lucide-react";
import { toast } from "sonner";

function EndpointCard({
  endpoint,
  isActive,
  onEdit,
  onDelete,
  onSetActive,
}: {
  endpoint: StacEndpoint;
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
      const res = await fetch(endpoint.url.replace(/\/+$/, "") + "/");
      if (res.ok) {
        setStatus("ok");
        toast.success(`Connected to ${endpoint.name}`);
      } else {
        setStatus("error");
        toast.error(`Failed: ${res.status} ${res.statusText}`);
      }
    } catch (err) {
      setStatus("error");
      toast.error(`Cannot reach ${endpoint.url}`);
    }
    setTesting(false);
  };

  return (
    <Card className={isActive ? "border-primary" : ""}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">{endpoint.name}</CardTitle>
            {isActive && <Badge variant="default">Active</Badge>}
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="icon" onClick={onEdit} aria-label="Edit endpoint">
              <Pencil className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={onDelete} aria-label="Delete endpoint">
              <Trash2 className="h-4 w-4 text-destructive" />
            </Button>
          </div>
        </div>
        <CardDescription className="font-mono text-xs">
          {endpoint.url}
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

function EndpointManagerInner() {
  const endpoints = useStore($endpoints);
  const active = useStore($activeEndpoint);
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<StacEndpoint | undefined>();
  const [deleteTarget, setDeleteTarget] = useState<StacEndpoint | null>(null);

  const handleAdd = (data: { name: string; url: string }) => {
    addEndpoint({ ...data, isDefault: endpoints.length === 0 });
    toast.success(`Added endpoint: ${data.name}`);
  };

  const handleEdit = (data: { name: string; url: string }) => {
    if (!editing) return;
    updateEndpoint(editing.id, data);
    toast.success(`Updated endpoint: ${data.name}`);
    setEditing(undefined);
  };

  const handleDelete = () => {
    if (!deleteTarget) return;
    removeEndpoint(deleteTarget.id);
    toast.success(`Removed endpoint: ${deleteTarget.name}`);
    setDeleteTarget(null);
  };

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-4xl mx-auto w-full">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold">STAC Endpoints</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Manage your STAC API connections
            </p>
          </div>
          <Button onClick={() => setFormOpen(true)}>
            <Plus className="h-4 w-4 mr-1.5" />
            Add Endpoint
          </Button>
        </div>

        {endpoints.length === 0 ? (
          <Card className="border-dashed">
            <CardContent className="flex flex-col items-center justify-center py-12 text-center">
              <Globe className="h-12 w-12 text-muted-foreground mb-4" />
              <h3 className="text-lg font-medium mb-2">No endpoints configured</h3>
              <p className="text-sm text-muted-foreground mb-4 max-w-sm">
                Add a STAC API endpoint to get started browsing and managing your
                spatiotemporal data.
              </p>
              <Button onClick={() => setFormOpen(true)}>
                <Plus className="h-4 w-4 mr-1.5" />
                Add Your First Endpoint
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4">
            {endpoints.map((ep) => (
              <EndpointCard
                key={ep.id}
                endpoint={ep}
                isActive={active?.id === ep.id}
                onEdit={() => {
                  setEditing(ep);
                }}
                onDelete={() => setDeleteTarget(ep)}
                onSetActive={() => setActiveEndpoint(ep.id)}
              />
            ))}
          </div>
        )}

        <EndpointForm
          open={formOpen}
          onOpenChange={setFormOpen}
          onSubmit={handleAdd}
        />

        {editing && (
          <EndpointForm
            open={!!editing}
            onOpenChange={(open) => !open && setEditing(undefined)}
            onSubmit={handleEdit}
            initial={editing}
          />
        )}

        <Dialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Delete Endpoint</DialogTitle>
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

export function EndpointManagerPage() {
  return (
    <QueryProvider>
      <EndpointManagerInner />
    </QueryProvider>
  );
}
