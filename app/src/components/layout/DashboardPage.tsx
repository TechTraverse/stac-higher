import { useStore } from "@nanostores/react";
import { $activeEndpoint, $endpoints } from "@/stores/endpointStore";
import { useLandingPage } from "@/lib/query/search";
import { useCollections } from "@/lib/query/collections";
import { QueryProvider } from "./QueryProvider";
import { Header } from "./Header";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Layers,
  Search,
  ArrowRight,
  CheckCircle2,
  AlertCircle,
  Settings,
} from "lucide-react";

function DashboardContent() {
  const endpoint = useStore($activeEndpoint);
  const endpoints = useStore($endpoints);
  const endpointUrl = endpoint?.url ?? "";

  const { data: landing, isLoading: landingLoading, error: landingError } =
    useLandingPage(endpointUrl);
  const { data: collections, isLoading: collectionsLoading } =
    useCollections(endpointUrl);

  if (endpoints.length === 0) {
    return (
      <main className="flex-1 p-6">
        <div className="max-w-2xl mx-auto text-center py-20">
          <div className="h-16 w-16 rounded-2xl bg-primary/10 flex items-center justify-center mx-auto mb-6">
            <Layers className="h-8 w-8 text-primary" />
          </div>
          <h1 className="text-3xl font-bold mb-3">Welcome to STAC Higher</h1>
          <p className="text-muted-foreground mb-8 text-lg">
            A modern interface for managing SpatioTemporal Asset Catalogs.
            Connect to a STAC API to get started.
          </p>
          <a href="/endpoints">
            <Button size="lg">
              <Settings className="h-4 w-4 mr-2" />
              Add Your First Endpoint
            </Button>
          </a>
        </div>
      </main>
    );
  }

  const collectionCount = collections?.collections?.length ?? 0;
  const connected = !landingError && !!landing;

  return (
    <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
      <div className="mb-8">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {endpoint?.name ?? "No endpoint selected"}
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-3 mb-8">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>API Status</CardDescription>
            <CardTitle className="flex items-center gap-2">
              {landingLoading ? (
                <Skeleton className="h-6 w-24" />
              ) : connected ? (
                <>
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                  Connected
                </>
              ) : (
                <>
                  <AlertCircle className="h-5 w-5 text-destructive" />
                  Disconnected
                </>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-xs text-muted-foreground font-mono truncate">
              {endpointUrl}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Collections</CardDescription>
            <CardTitle>
              {collectionsLoading ? (
                <Skeleton className="h-8 w-12" />
              ) : (
                collectionCount
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <a
              href="/collections"
              className="text-xs text-primary hover:underline inline-flex items-center gap-1"
            >
              Browse collections <ArrowRight className="h-3 w-3" />
            </a>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardDescription>STAC Version</CardDescription>
            <CardTitle>
              {landingLoading ? (
                <Skeleton className="h-8 w-16" />
              ) : (
                landing?.stac_version ?? "N/A"
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {landing?.conformsTo && (
              <p className="text-xs text-muted-foreground">
                {landing.conformsTo.length} conformance classes
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <h2 className="text-lg font-semibold mb-4">Quick Actions</h2>
      <div className="grid gap-4 md:grid-cols-3">
        <a href="/collections" className="block">
          <Card className="hover:bg-accent/50 transition-colors cursor-pointer h-full">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Layers className="h-5 w-5" />
                Browse Collections
              </CardTitle>
              <CardDescription>
                View and manage your STAC collections
              </CardDescription>
            </CardHeader>
          </Card>
        </a>

        <a href="/collections/new" className="block">
          <Card className="hover:bg-accent/50 transition-colors cursor-pointer h-full">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Layers className="h-5 w-5" />
                Create Collection
              </CardTitle>
              <CardDescription>
                Add a new collection to the catalog
              </CardDescription>
            </CardHeader>
          </Card>
        </a>

        <a href="/search" className="block">
          <Card className="hover:bg-accent/50 transition-colors cursor-pointer h-full">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Search className="h-5 w-5" />
                Search Items
              </CardTitle>
              <CardDescription>
                Search across collections with spatial and temporal filters
              </CardDescription>
            </CardHeader>
          </Card>
        </a>
      </div>

      {connected && landing?.conformsTo && (
        <div className="mt-8">
          <h2 className="text-lg font-semibold mb-4">Conformance</h2>
          <div className="flex flex-wrap gap-2">
            {landing.conformsTo.map((c) => {
              const shortName = c.split("/").pop() ?? c;
              return (
                <Badge key={c} variant="secondary" className="text-xs">
                  {shortName}
                </Badge>
              );
            })}
          </div>
        </div>
      )}
    </main>
  );
}

export function DashboardPage() {
  return (
    <QueryProvider>
      <Header />
      <DashboardContent />
    </QueryProvider>
  );
}
