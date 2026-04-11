import { useState } from "react";
import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import { useCollections } from "@/lib/query/collections";
import { useStacSearch } from "@/lib/query/search";
import type { StacSearchBody } from "@/lib/stac-api/types";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { StacMap } from "@/components/map/StacMap";
import { FootprintLayer } from "@/components/map/FootprintLayer";
import { ItemCard } from "@/components/items/ItemCard";
import { BboxInput } from "@/components/shared/BboxInput";
import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Search, X } from "lucide-react";

function SearchInner() {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
  const { data: collectionsData } = useCollections(endpointUrl);
  const collections = collectionsData?.collections ?? [];

  const [selectedCollections, setSelectedCollections] = useState<string[]>([]);
  const [bbox, setBbox] = useState<number[]>([-180, -90, 180, 90]);
  const [useBbox, setUseBbox] = useState(false);
  const [datetimeStart, setDatetimeStart] = useState("");
  const [datetimeEnd, setDatetimeEnd] = useState("");
  const [cqlFilter, setCqlFilter] = useState("");
  const [useCql, setUseCql] = useState(false);
  const [searchParams, setSearchParams] = useState<StacSearchBody | null>(null);
  const [selectedItemId, setSelectedItemId] = useState<string | undefined>();

  const { data: searchData, isLoading, error, refetch } = useStacSearch(
    endpointUrl,
    searchParams,
  );

  const handleSearch = () => {
    const params: StacSearchBody = { limit: 50 };

    if (selectedCollections.length > 0) {
      params.collections = selectedCollections;
    }

    if (useBbox && bbox.length === 4) {
      params.bbox = bbox;
    }

    if (datetimeStart || datetimeEnd) {
      const start = datetimeStart
        ? new Date(datetimeStart).toISOString()
        : "..";
      const end = datetimeEnd ? new Date(datetimeEnd).toISOString() : "..";
      params.datetime = `${start}/${end}`;
    }

    if (useCql && cqlFilter.trim()) {
      params.filter = cqlFilter.trim() as unknown as Record<string, unknown>;
      params["filter-lang"] = "cql2-text";
    }

    setSearchParams(params);
  };

  const handleClear = () => {
    setSelectedCollections([]);
    setBbox([-180, -90, 180, 90]);
    setUseBbox(false);
    setDatetimeStart("");
    setDatetimeEnd("");
    setCqlFilter("");
    setUseCql(false);
    setSearchParams(null);
    setSelectedItemId(undefined);
  };

  const toggleCollection = (id: string) => {
    setSelectedCollections((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id],
    );
  };

  const items = searchData?.features ?? [];

  return (
    <>
      <Header />
      <main className="flex-1 flex flex-col lg:flex-row lg:overflow-hidden lg:h-[calc(100vh-3.5rem)]">
        <div className="w-full lg:w-[380px] shrink-0 lg:border-r border-b lg:border-b-0 border-border lg:overflow-y-auto p-4 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold">Search</h2>
            {searchParams && (
              <Button variant="ghost" size="sm" onClick={handleClear}>
                <X className="h-3.5 w-3.5 mr-1" />
                Clear
              </Button>
            )}
          </div>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Collections</CardTitle>
            </CardHeader>
            <CardContent>
              {collections.length === 0 ? (
                <p className="text-xs text-muted-foreground">No collections available</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {collections.map((c) => (
                    <Badge
                      key={c.id}
                      variant={
                        selectedCollections.includes(c.id)
                          ? "default"
                          : "outline"
                      }
                      className="cursor-pointer text-xs"
                      onClick={() => toggleCollection(c.id)}
                    >
                      {c.title || c.id}
                    </Badge>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">Temporal Range</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-1">
                <Label className="text-xs">Start</Label>
                <Input
                  type="datetime-local"
                  value={datetimeStart}
                  onChange={(e) => setDatetimeStart(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">End</Label>
                <Input
                  type="datetime-local"
                  value={datetimeEnd}
                  onChange={(e) => setDatetimeEnd(e.target.value)}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">Spatial Filter (Bbox)</CardTitle>
                <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useBbox}
                    onChange={(e) => setUseBbox(e.target.checked)}
                    className="rounded"
                  />
                  Enable
                </label>
              </div>
            </CardHeader>
            {useBbox && (
              <CardContent>
                <BboxInput value={bbox} onChange={setBbox} />
              </CardContent>
            )}
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm">CQL2 Filter</CardTitle>
                <label className="flex items-center gap-1.5 text-xs cursor-pointer">
                  <input
                    type="checkbox"
                    checked={useCql}
                    onChange={(e) => setUseCql(e.target.checked)}
                    className="rounded"
                  />
                  Enable
                </label>
              </div>
            </CardHeader>
            {useCql && (
              <CardContent>
                <Textarea
                  value={cqlFilter}
                  onChange={(e) => setCqlFilter(e.target.value)}
                  placeholder={'e.g. eo:cloud_cover < 20'}
                  rows={3}
                  className="font-mono text-xs"
                />
                <p className="text-[10px] text-muted-foreground mt-1.5">
                  CQL2-Text expression for property filtering
                </p>
              </CardContent>
            )}
          </Card>

          <Button className="w-full" onClick={handleSearch}>
            <Search className="h-4 w-4 mr-1.5" />
            Search
          </Button>

          {searchParams && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">
                  Results
                  {searchData?.context?.matched !== undefined &&
                    ` (${searchData.context.matched} total)`}
                </h3>
              </div>

              {isLoading ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <Skeleton key={i} className="h-20" />
                  ))}
                </div>
              ) : error ? (
                <ErrorState
                  message={
                    error instanceof Error
                      ? error.message
                      : "Search failed"
                  }
                  onRetry={() => refetch()}
                />
              ) : items.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">
                  No items found matching your criteria.
                </p>
              ) : (
                <div className="space-y-2">
                  {items.map((item) => (
                    <div
                      key={item.id}
                      onMouseEnter={() => setSelectedItemId(item.id)}
                      onMouseLeave={() => setSelectedItemId(undefined)}
                    >
                      <ItemCard
                        item={item}
                        collectionId={item.collection ?? "unknown"}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="h-[400px] lg:h-auto lg:flex-1 relative">
          <StacMap className="h-full w-full">
            {items.length > 0 && (
              <FootprintLayer items={items} selectedId={selectedItemId} />
            )}
          </StacMap>
        </div>
      </main>
    </>
  );
}

export function SearchPageComponent() {
  return (
    <QueryProvider>
      <SearchInner />
    </QueryProvider>
  );
}
