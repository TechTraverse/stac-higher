/**
 * Home — the product-centric overview (ADR 0017 / UI-3).
 *
 * Four stat tiles, a connection-health strip, and the product list with a
 * per-product health verdict and mini lineage glyph. Everything is derived in
 * `overview.ts` from three reads the app already makes (graph, flows, open
 * alerts) plus the catalog's collection list — no new endpoints.
 *
 * The old dashboard's catalog cards are not deleted: API status, STAC version
 * and the conformance classes move into the compact "Stack" strip at the
 * bottom, which is where a catalog-level fact belongs once the page is about
 * products.
 *
 * Terminology: copy says "product"; the code, routes and API still say
 * collection (ADR 0017 §3).
 */
import { useStore } from "@nanostores/react";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  Database,
  Layers,
  Plug,
  Plus,
} from "lucide-react";
import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  LineageStrip,
  Skeleton,
  healthDotClass,
  type LineageHealth,
} from "@stac-higher/shared";
import { AppShell } from "@/components/layout/AppShell";
import { $activeCatalog, $catalogs } from "@/stores/catalogStore";
import { useCollections } from "@/lib/query/collections";
import { useLandingPage } from "@/lib/query/search";
import { useAlerts, useFlows } from "@/lib/monitoring/queries";
import { usePipelineGraph } from "@/lib/monitoring/graph-queries";
import { ALERTS_PAGE_LIMIT } from "@/lib/monitoring/api";
import {
  buildConnectionChips,
  buildProductRows,
  buildStats,
  type ProductRow,
} from "@/components/layout/overview";

const HEALTH_LABEL: Record<LineageHealth, string> = {
  ok: "Healthy",
  warn: "Degraded",
  error: "Failing",
  unknown: "Unknown",
};

const HEALTH_TEXT: Record<LineageHealth, string> = {
  ok: "text-success",
  warn: "text-warning",
  error: "text-danger",
  unknown: "text-muted-foreground",
};

function StatTile({
  label,
  value,
  sub,
  subTone = "muted",
  href,
  loading,
}: {
  label: string;
  value: number | string;
  sub?: string;
  subTone?: "muted" | "warn" | "error";
  href?: string;
  loading?: boolean;
}) {
  const body = (
    <Card className="h-full">
      <CardContent className="px-5 py-4">
        <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-muted-foreground">
          {label}
        </div>
        <div className="mt-1.5 flex items-baseline gap-2">
          {loading ? (
            <Skeleton className="h-7 w-14" />
          ) : (
            <span className="text-[28px] font-extrabold leading-none tracking-tight tabular-nums">
              {value}
            </span>
          )}
          {sub && !loading && (
            <span
              className={
                subTone === "error"
                  ? "text-[13px] font-semibold text-danger"
                  : subTone === "warn"
                    ? "text-[13px] font-semibold text-warning"
                    : "text-[13px] font-medium text-muted-foreground"
              }
            >
              {sub}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
  return href ? (
    <a href={href} className="block transition-colors hover:[&_[data-slot=card]]:border-primary/50">
      {body}
    </a>
  ) : (
    body
  );
}

function ConnectionStrip() {
  const { data: graph, isLoading } = usePipelineGraph();
  const { data: flows } = useFlows();
  const { data: openAlerts } = useAlerts("open");
  const chips = buildConnectionChips(graph, flows, openAlerts);

  if (isLoading) return <Skeleton className="h-24" />;
  if (chips.length === 0) return null;

  return (
    <Card>
      <CardContent className="px-5 py-4">
        <div className="mb-3 flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <h2 className="text-sm font-bold">Connection health</h2>
          <p className="text-xs text-muted-foreground">
            ingest sources and distribution destinations
          </p>
          <a
            href="/connections"
            className="ml-auto text-[13px] font-semibold text-primary hover:underline"
          >
            Manage connections
          </a>
        </div>
        <div className="flex flex-wrap gap-2">
          {chips.map((chip) => (
            <a
              key={chip.id}
              href="/connections"
              className="flex items-center gap-2 rounded-sm border border-border bg-background px-3 py-1.5 transition-colors hover:border-primary/50"
            >
              <span
                aria-hidden="true"
                className={`h-2 w-2 shrink-0 rounded-full ${healthDotClass(chip.health)}`}
              />
              <span className="text-[13px] font-semibold">{chip.name}</span>
              <span className="tech rounded bg-muted px-1.5 py-px text-[10.5px] text-muted-foreground">
                {chip.protocol}
              </span>
              {chip.directions.length > 0 && (
                <span className="text-[11.5px] text-muted-foreground">
                  {chip.directions.join(" · ")}
                </span>
              )}
            </a>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ProductListRow({ product }: { product: ProductRow }) {
  return (
    <a
      href={`/collections/${encodeURIComponent(product.id)}`}
      data-testid={`product-row-${product.id}`}
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-5 py-3 last:border-b-0 hover:bg-accent/50"
    >
      <div className="min-w-0 flex-1 basis-64">
        <div className="truncate text-[13.5px] font-semibold">{product.title}</div>
        <div className="tech truncate text-[11px] text-muted-foreground">
          {product.id}
        </div>
      </div>

      <div className="w-28 shrink-0 text-right sm:text-left">
        {product.itemsIngested === null ? (
          <span className="text-xs text-muted-foreground/60">—</span>
        ) : (
          <>
            <span className="tech text-[12px] tabular-nums">
              {product.itemsIngested.toLocaleString()}
            </span>
            <span className="ml-1 text-[11px] text-muted-foreground">
              ingested
            </span>
          </>
        )}
      </div>

      <div className="min-w-0 flex-1 basis-64">
        <LineageStrip size="mini" groups={product.lineage} />
      </div>

      <div className="flex w-40 shrink-0 items-center gap-2">
        <span
          aria-hidden="true"
          className={`h-2.5 w-2.5 shrink-0 rounded-full ${healthDotClass(product.health)}`}
        />
        <span className="min-w-0">
          <span
            className={`block text-[12.5px] font-semibold ${HEALTH_TEXT[product.health]}`}
          >
            {HEALTH_LABEL[product.health]}
          </span>
          {product.reason && (
            <span className="block truncate text-[11px] text-muted-foreground">
              {product.reason}
            </span>
          )}
        </span>
      </div>
    </a>
  );
}

/**
 * The catalog-level facts the old dashboard led with. They are still true and
 * still wanted — just not the headline once the page is about products.
 */
function StackStrip() {
  const catalog = useStore($activeCatalog);
  const endpointUrl = catalog?.url ?? "";
  const { data: landing, isLoading, error } = useLandingPage(endpointUrl);
  const [expanded, setExpanded] = useState(false);
  const connected = !error && !!landing;

  return (
    <Card>
      <CardContent className="px-5 py-3.5">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px]">
          <span className="flex items-center gap-2">
            {isLoading ? (
              <Skeleton className="h-4 w-24" />
            ) : (
              <>
                <span
                  aria-hidden="true"
                  className={`h-2 w-2 rounded-full ${connected ? "bg-success" : "bg-danger"}`}
                />
                <span className="font-semibold">
                  {connected ? "STAC API connected" : "STAC API unreachable"}
                </span>
              </>
            )}
          </span>
          <span className="tech truncate text-[11.5px] text-muted-foreground">
            {endpointUrl}
          </span>
          {landing?.stac_version && (
            <span className="text-muted-foreground">
              STAC <span className="tech">{landing.stac_version}</span>
            </span>
          )}
          {landing?.conformsTo && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="ml-auto inline-flex items-center gap-1 text-[12.5px] font-semibold text-primary hover:underline"
            >
              {landing.conformsTo.length} conformance classes
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
              />
            </button>
          )}
        </div>
        {expanded && landing?.conformsTo && (
          <div className="mt-3 flex flex-wrap gap-1.5 border-t border-border pt-3">
            {landing.conformsTo.map((c) => (
              <Badge key={c} variant="secondary" className="tech text-[10.5px]">
                {c.split("/").pop() ?? c}
              </Badge>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DashboardContent() {
  const catalog = useStore($activeCatalog);
  const catalogs = useStore($catalogs);
  const endpointUrl = catalog?.url ?? "";

  const { data: collections, isLoading: collectionsLoading } =
    useCollections(endpointUrl);
  const { data: graph, isLoading: graphLoading } = usePipelineGraph();
  const { data: flows } = useFlows();
  const { data: openAlerts, isLoading: alertsLoading, isError: alertsError } =
    useAlerts("open");

  if (catalogs.length === 0) {
    return (
      <main className="flex-1 p-6">
        <div className="mx-auto max-w-2xl py-20 text-center">
          <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
            <Layers className="h-8 w-8 text-primary" />
          </div>
          <h1 className="mb-3 text-3xl font-bold">Welcome to STAC Higher</h1>
          <p className="mb-8 text-lg text-muted-foreground">
            A modern interface for managing SpatioTemporal Asset Catalogs.
            Connect to a STAC catalog to get started.
          </p>
          <a href="/catalogs">
            <Button size="lg">
              <Database className="mr-2 h-4 w-4" />
              Add Your First Catalog
            </Button>
          </a>
        </div>
      </main>
    );
  }

  const chips = buildConnectionChips(graph, flows, openAlerts);
  const stats = buildStats(graph, flows, chips);
  // "Healthy" is only claimed on a loaded, untruncated alert list — the same
  // evidence rule the /monitoring flows card uses for its on-time hint.
  const alertsAreComplete =
    !alertsLoading &&
    !alertsError &&
    (openAlerts?.length ?? 0) < ALERTS_PAGE_LIMIT;
  const { rows: products, unattributed } = buildProductRows({
    collections: collections?.collections ?? [],
    graph,
    flows,
    openAlerts,
    alertsAreComplete,
  });

  return (
    <main className="w-full flex-1 space-y-5 p-6">
      <div className="grid gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Products"
          value={products.length}
          href="/collections"
          loading={collectionsLoading}
        />
        <StatTile
          label="Connections"
          value={stats.connections}
          sub={
            stats.connectionsDegraded > 0
              ? `${stats.connectionsDegraded} degraded`
              : undefined
          }
          subTone="error"
          href="/connections"
          loading={graphLoading}
        />
        <StatTile
          label="Processes"
          value={stats.processes}
          sub={
            stats.processesUndeployed > 0
              ? `${stats.processesUndeployed} not deployed`
              : undefined
          }
          subTone="warn"
          href="/processes"
          loading={graphLoading}
        />
        {/* The mockup's "Ingest (24h)" needs a 24h rollup no current response
            carries; `flow_stats` is cumulative. The honest all-time total goes
            here rather than a new endpoint (UI-3 scope rule). */}
        <StatTile
          label="Items ingested"
          value={stats.itemsIngested.toLocaleString()}
          sub="all time"
          href="/monitoring"
          loading={graphLoading}
        />
      </div>

      <ConnectionStrip />

      {unattributed.length > 0 && (
        <Card className="border-warning-border bg-warning-subtle">
          <CardContent className="flex flex-wrap items-center gap-3 px-5 py-3">
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
            <p className="text-[13px]">
              {unattributed.length === 1
                ? "1 open alert isn't shown against a product"
                : `${unattributed.length} open alerts aren't shown against a product`}
              {" — process alerts carry no product anchor in the alerts API."}
            </p>
            <a
              href="/monitoring"
              className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-primary hover:underline"
            >
              See them in Monitoring <ArrowRight className="h-3 w-3" />
            </a>
          </CardContent>
        </Card>
      )}

      <Card className="overflow-hidden py-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 px-5 pb-3 pt-4">
          <h2 className="text-sm font-bold">Products</h2>
          <span className="tech text-[11.5px] text-muted-foreground">
            {catalog?.name ?? "no catalog"} · {products.length}{" "}
            {products.length === 1 ? "collection" : "collections"}
          </span>
          <a
            href="/collections/new"
            className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-primary hover:underline"
          >
            <Plus className="h-3.5 w-3.5" />
            New product
          </a>
        </div>

        {collectionsLoading ? (
          <div className="space-y-2 px-5 pb-5">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12" />
            ))}
          </div>
        ) : products.length === 0 ? (
          <div className="px-5 pb-6 pt-2 text-sm text-muted-foreground">
            This catalog has no collections yet.
          </div>
        ) : (
          <div className="border-t border-border">
            {products.map((product) => (
              <ProductListRow key={product.id} product={product} />
            ))}
          </div>
        )}
      </Card>

      {chips.length === 0 && !graphLoading && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 px-5 py-4">
            <Plug className="h-4 w-4 shrink-0 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No sources or destinations are wired yet. Connections are how
              products get their items and how they reach downstream systems.
            </p>
            <a
              href="/connections"
              className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-primary hover:underline"
            >
              Add a connection <ArrowRight className="h-3 w-3" />
            </a>
          </CardContent>
        </Card>
      )}

      <StackStrip />
    </main>
  );
}

export function DashboardPage() {
  return (
    <AppShell>
      <DashboardContent />
    </AppShell>
  );
}
