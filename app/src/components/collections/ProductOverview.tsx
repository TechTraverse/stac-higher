/**
 * The product Overview panel (ADR 0017 / UI-4) — the platform view of a
 * built-in-catalog collection, rendered above the STAC metadata that the
 * Overview tab already showed.
 *
 * External-catalog collections never see this: they have no associations, no
 * settings row and no place in the pipeline graph, so the tab keeps its
 * original catalog-browser content.
 *
 * Health is derived by the SAME `buildProductRows` the home overview uses, fed
 * this collection's own associations. One derivation, so the product page and
 * the home list cannot render different verdicts for the same product.
 *
 * Terminology: copy says product / source / destination; the code, routes and
 * API still say collection / association (ADR 0017 §3).
 */
import {
  Badge,
  Card,
  CardContent,
  LineageStrip,
  Skeleton,
  healthDotClass,
  type LineageHealth,
} from "@stac-higher/shared";
import { AlertTriangle, ArrowRight, ExternalLink, Waves } from "lucide-react";
import type { StacCollection, StacItem } from "@/lib/stac-api/types";
import { useAssociations } from "@/lib/associations/queries";
import { useCollectionSettings } from "@/lib/collections/settings-client";
import { useAlerts } from "@/lib/monitoring/queries";
import { usePipelineGraph, useFlowHistory } from "@/lib/monitoring/graph-queries";
import { ALERTS_PAGE_LIMIT } from "@/lib/monitoring/api";
import {
  buildProductRows,
  successRate,
  unanchoredAlerts,
} from "@/components/layout/overview";

const HEALTH_LABEL: Record<LineageHealth, string> = {
  ok: "Healthy",
  warn: "Degraded",
  error: "Failing",
  unknown: "Unknown",
};

const HEALTH_BADGE: Record<LineageHealth, string> = {
  ok: "border-success-border bg-success-subtle text-success",
  warn: "border-warning-border bg-warning-subtle text-warning",
  error: "border-danger-border bg-danger-subtle text-danger",
  unknown: "border-border bg-muted text-muted-foreground",
};

/** Browser-facing OGC bases; defaults match the compose stack (docs/serving.md). */
const TITILER_URL =
  import.meta.env.PUBLIC_TITILER_URL ?? "http://localhost:8084";
const TIPG_URL = import.meta.env.PUBLIC_TIPG_URL ?? "http://localhost:8085";

function EndpointRow({
  label,
  href,
  note,
}: {
  label: string;
  href: string;
  note?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 py-1.5">
      <span className="w-32 shrink-0 text-[12.5px] font-semibold">{label}</span>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="tech inline-flex min-w-0 items-center gap-1 break-all text-[12px] text-primary hover:underline"
      >
        {href}
        <ExternalLink className="h-3 w-3 shrink-0" />
      </a>
      {note && (
        <span className="text-[11.5px] text-muted-foreground">{note}</span>
      )}
    </div>
  );
}

export function ProductOverview({
  collection,
  collectionId,
  endpointUrl,
  items,
  onOpenDataFlow,
}: {
  collection: StacCollection;
  collectionId: string;
  endpointUrl: string;
  /** First page of the items query the detail page already runs. */
  items: StacItem[];
  /** Switches the page's tab strip to Data flow. */
  onOpenDataFlow: () => void;
}) {
  const { data: associations, isLoading: assocLoading } =
    useAssociations(collectionId);
  const { data: graph } = usePipelineGraph();
  const {
    data: openAlerts,
    isLoading: alertsLoading,
    isError: alertsError,
  } = useAlerts("open");
  const { data: settings } = useCollectionSettings(collectionId);

  const flows = associations ?? [];
  // One history request, not one per association: the hook is `enabled` on a
  // non-null subject, so a product with no flows simply never fetches.
  const primary =
    flows.find((f) => f.direction === "ingest") ?? flows[0] ?? null;
  const { data: history } = useFlowHistory("association", primary?.id ?? null, 30);
  const rate = primary ? successRate(primary.direction, history) : null;

  const alertsAreComplete =
    !alertsLoading &&
    !alertsError &&
    (openAlerts?.length ?? 0) < ALERTS_PAGE_LIMIT;

  const {
    rows: [product],
  } = buildProductRows({
    collections: [{ id: collectionId, title: collection.title }],
    graph,
    flows,
    openAlerts,
    alertsAreComplete,
  });

  const health = product?.health ?? "unknown";
  const processCount =
    product?.lineage.find((g) => g.kind === "process")?.nodes.length ?? 0;
  // A process alert cannot be tied to a product through the alerts API, so a
  // product WITH processes can only say "one of these might be mine".
  const maybeMine = processCount > 0 ? unanchoredAlerts(openAlerts) : [];
  const sources = flows.filter((f) => f.direction === "ingest");
  const destinations = flows.filter((f) => f.direction === "deliver");
  const stacHref = `${endpointUrl.replace(/\/$/, "")}/collections/${encodeURIComponent(collectionId)}`;

  return (
    <div className="space-y-4">
      {/* -- health + visibility ------------------------------------------ */}
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center gap-2 rounded-sm border px-3 py-1 text-[12.5px] font-bold ${HEALTH_BADGE[health]}`}
        >
          <span
            aria-hidden="true"
            className={`h-2 w-2 rounded-full ${healthDotClass(health)}`}
          />
          {HEALTH_LABEL[health]}
          {rate !== null && ` · ${rate.toFixed(1)}% (30d)`}
        </span>
        {product?.reason && (
          <span className="text-[12.5px] text-muted-foreground">
            {product.reason}
          </span>
        )}
        <Badge variant="outline" className="text-[11.5px]">
          built-in catalog
        </Badge>
        {settings?.archived && (
          <Badge variant="outline" className="text-[11.5px]">
            archived
          </Badge>
        )}
        {settings?.externallyWritable && (
          <Badge variant="outline" className="text-[11.5px]">
            externally writable
          </Badge>
        )}
        {settings?.retentionDays != null && (
          <Badge variant="outline" className="text-[11.5px]">
            retention {settings.retentionDays}d
          </Badge>
        )}
      </div>

      {maybeMine.length > 0 && (
        <Card className="border-warning-border bg-warning-subtle">
          <CardContent className="flex flex-wrap items-center gap-3 px-5 py-3">
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
            <p className="text-[13px]">
              {maybeMine.length === 1
                ? "1 open process alert may relate to this product"
                : `${maybeMine.length} open process alerts may relate to this product`}
              {" — the alerts API doesn't say which process an alert belongs to."}
            </p>
            <a
              href="/monitoring"
              className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-primary hover:underline"
            >
              Check Monitoring <ArrowRight className="h-3 w-3" />
            </a>
          </CardContent>
        </Card>
      )}

      {/* -- lineage & distribution ---------------------------------------- */}
      <Card>
        <CardContent className="px-5 py-4">
          <div className="mb-4 flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <Waves className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-bold">Lineage &amp; distribution</h2>
            <span className="text-xs text-muted-foreground">
              {sources.length} {sources.length === 1 ? "source" : "sources"} ·{" "}
              {destinations.length}{" "}
              {destinations.length === 1 ? "destination" : "destinations"}
            </span>
            <button
              type="button"
              onClick={onOpenDataFlow}
              className="ml-auto inline-flex items-center gap-1 text-[13px] font-semibold text-primary hover:underline"
            >
              Manage in Data flow <ArrowRight className="h-3 w-3" />
            </button>
          </div>
          {assocLoading ? (
            <Skeleton className="h-24" />
          ) : (
            <LineageStrip
              size="medium"
              groups={product?.lineage ?? []}
              emptyLabel="No sources, processes or destinations are wired to this product yet."
            />
          )}
        </CardContent>
      </Card>

      {/* -- endpoints ------------------------------------------------------ */}
      <Card>
        <CardContent className="px-5 py-4">
          <h2 className="mb-2 text-sm font-bold">Endpoints</h2>
          <div className="divide-y divide-border">
            <EndpointRow label="STAC collection" href={stacHref} />
            <EndpointRow label="STAC items" href={`${stacHref}/items`} />
            {settings?.servingEnabled ? (
              <>
                <EndpointRow
                  label="Raster tiles"
                  href={`${TITILER_URL}/collections/${encodeURIComponent(collectionId)}/info`}
                  note="titiler-pgstac"
                />
                <EndpointRow
                  label="Vector features"
                  href={`${TIPG_URL}/`}
                  note="tipg — stack-wide, not per-product"
                />
              </>
            ) : (
              <p className="py-2 text-[12.5px] text-muted-foreground">
                OGC serving links are not advertised for this product. Turn on
                serving in <strong>Settings</strong> to show them.
              </p>
            )}
          </div>
          {settings?.servingEnabled && (
            <p className="mt-2 border-t border-border pt-2 text-[11.5px] text-muted-foreground">
              Display only — the toggle advertises these endpoints, it does not
              gate the services. Rasters tile only where item asset hrefs are
              absolute (platform <span className="tech">/api/assets/…</span>{" "}
              hrefs are not resolvable by the tiler).
            </p>
          )}
        </CardContent>
      </Card>

      {/* -- recent items --------------------------------------------------- */}
      {items.length > 0 && (
        <Card>
          <CardContent className="px-5 py-4">
            <div className="mb-3 flex flex-wrap items-center gap-x-2.5 gap-y-1">
              <h2 className="text-sm font-bold">Recent items</h2>
              <span className="text-xs text-muted-foreground">
                newest first
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              {items.slice(0, 8).map((item) => (
                <a
                  key={item.id}
                  href={`/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(item.id)}`}
                  className="tech max-w-64 truncate rounded-sm border border-border bg-background px-2.5 py-1.5 text-[11.5px] transition-colors hover:border-primary/50"
                  title={item.id}
                >
                  {item.id}
                </a>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
