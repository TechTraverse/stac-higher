/**
 * Home-overview derivations (UI-3): the adapter between the platform reads
 * the app already makes — `/api/monitoring/graph`, `/api/monitoring/flows`,
 * `/api/alerts?state=open` — and the presentational shapes the overview
 * renders.
 *
 * NO new endpoints: every number here is computed from those three responses
 * plus the catalog's collection list. All three are group-scoped server-side,
 * so the overview shows the caller's own world rather than a global total.
 *
 * Health verdicts defer to the monitor: an open alert row IS the verdict, and
 * lateness comes from `isLate` (the monitor's expectation-breach alert), never
 * from a local re-derivation. That is what keeps this page and /monitoring
 * from disagreeing.
 */
import type { LineageGroup, LineageHealth } from "@stac-higher/shared";
import type { Association } from "@/lib/associations/types";
import type { Alert } from "@/lib/monitoring/api";
import type { DailyStats, PipelineGraph } from "@/lib/monitoring/graph-api";
import { collectionNode } from "@/lib/graph/edges";
import { alertKindLabel, isLate, readFlowStats } from "@/components/monitoring/shared";

export interface ConnectionChip {
  id: string;
  name: string;
  protocol: string;
  /** Directions this connection is actually wired for, from the flow list. */
  directions: Array<"ingest" | "deliver">;
  health: LineageHealth;
}

export interface ProductRow {
  id: string;
  title: string;
  /** Ingest-derived item count; null when the product has no ingest flows. */
  itemsIngested: number | null;
  health: LineageHealth;
  /** One line saying WHY, or null when there is nothing to say. */
  reason: string | null;
  lineage: LineageGroup[];
  /** True when the platform knows about this collection at all. */
  onPlatform: boolean;
}

const HEALTH_RANK: Record<LineageHealth, number> = {
  error: 3,
  warn: 2,
  ok: 1,
  unknown: 0,
};

function worse(a: LineageHealth, b: LineageHealth): LineageHealth {
  return HEALTH_RANK[a] >= HEALTH_RANK[b] ? a : b;
}

/** A firing alert is an error; an acknowledged one is a warning we still show. */
function alertHealth(alert: Alert): LineageHealth {
  return alert.state === "firing" ? "error" : "warn";
}

function connectionHealth(status: unknown): LineageHealth {
  if (status === "ok") return "ok";
  if (status === "error") return "error";
  return "unknown"; // "unverified" — not wrong, just unproven.
}

/**
 * The connection-health strip. Protocol and status come from the graph's
 * connection nodes; direction is NOT a property of a connection (it lives on
 * the association), so it is joined in from the flow list.
 */
export function buildConnectionChips(
  graph: PipelineGraph | undefined,
  flows: Association[] | undefined,
  openAlerts: Alert[] | undefined,
): ConnectionChip[] {
  if (!graph) return [];
  const directions = new Map<string, Set<"ingest" | "deliver">>();
  for (const flow of flows ?? []) {
    const set = directions.get(flow.connection_id) ?? new Set();
    set.add(flow.direction);
    directions.set(flow.connection_id, set);
  }
  const alertsByConnection = new Map<string, Alert>();
  for (const alert of openAlerts ?? []) {
    if (!alert.connection_id) continue;
    const current = alertsByConnection.get(alert.connection_id);
    if (!current || alertHealth(alert) === "error") {
      alertsByConnection.set(alert.connection_id, alert);
    }
  }

  return graph.nodes
    .filter((node) => node.type === "connection")
    .map((node) => {
      const id = node.id.replace(/^conn:/, "");
      const alert = alertsByConnection.get(id);
      return {
        id,
        name: node.label,
        protocol: String(node.meta.protocol ?? "—"),
        directions: [...(directions.get(id) ?? [])].sort(),
        health: worse(
          connectionHealth(node.meta.status),
          alert ? alertHealth(alert) : "unknown",
        ),
      };
    })
    .sort((a, b) => HEALTH_RANK[b.health] - HEALTH_RANK[a.health] || a.name.localeCompare(b.name));
}

interface ProductInput {
  /** Collections from the active catalog, in display order. */
  collections: Array<{ id: string; title?: string }>;
  graph: PipelineGraph | undefined;
  flows: Association[] | undefined;
  openAlerts: Alert[] | undefined;
  /** False while the alert list is loading, errored, or possibly truncated —
   * "healthy" then means "no evidence of a problem", not "proven fine". */
  alertsAreComplete: boolean;
}

export interface ProductRollup {
  rows: ProductRow[];
  /**
   * Open alerts no product could claim. Today that is every process-anchored
   * alert: `/api/alerts` returns connection / association / channel /
   * collection anchors but NOT `process_id` or `source_id`, even though
   * migration 024 stores them. Surfacing the count is the honest answer —
   * showing every product healthy while /monitoring shows a firing alert is
   * exactly the disagreement this page must not create.
   */
  unattributed: Alert[];
}

export function buildProductRows({
  collections,
  graph,
  flows,
  openAlerts,
  alertsAreComplete,
}: ProductInput): ProductRollup {
  const nodesById = new Map((graph?.nodes ?? []).map((n) => [n.id, n]));
  const edges = graph?.edges ?? [];
  const alerts = openAlerts ?? [];

  // association id -> the flow row, so an association-anchored alert can be
  // attributed to the collection it belongs to.
  const flowById = new Map((flows ?? []).map((f) => [f.id, f]));

  const claimed = new Set<string>();

  const rows = collections.map((collection) => {
    const nodeId = collectionNode(collection.id);
    const onPlatform = nodesById.has(nodeId);

    const sources = edges.filter((e) => e.kind === "ingest" && e.to === nodeId);
    const destinations = edges.filter(
      (e) => e.kind === "deliver" && e.from === nodeId,
    );
    const processes = edges.filter(
      (e) =>
        (e.kind === "process_source" && e.from === nodeId) ||
        (e.kind === "process_output" && e.to === nodeId),
    );

    const collectionFlows = (flows ?? []).filter(
      (f) => f.collection_id === collection.id,
    );

    // Alerts that belong to this product: anchored directly, through one of
    // its associations, or through a connection it is wired to.
    const wiredConnections = new Set(collectionFlows.map((f) => f.connection_id));
    const own = alerts.filter((a) => {
      const mine =
        a.collection_id === collection.id ||
        (!!a.association_id &&
          flowById.get(a.association_id)?.collection_id === collection.id) ||
        (!!a.connection_id && wiredConnections.has(a.connection_id));
      if (mine) claimed.add(a.id);
      return mine;
    });

    let health: LineageHealth = onPlatform
      ? alertsAreComplete
        ? "ok"
        : "unknown"
      : "unknown";
    let reason: string | null = null;

    const firing = own.find((a) => a.state === "firing");
    const acknowledged = own.find((a) => a.state === "acknowledged");
    const lateFlow = collectionFlows.find((f) =>
      isLate(f.direction, f.id, alerts),
    );

    if (firing) {
      health = "error";
      reason = alertKindLabel(firing.kind);
    } else if (acknowledged) {
      health = "warn";
      reason = `${alertKindLabel(acknowledged.kind)} (acknowledged)`;
    } else if (lateFlow) {
      health = "warn";
      reason = `${lateFlow.direction} behind its expectation`;
    } else if (onPlatform && sources.length === 0 && destinations.length === 0 && processes.length === 0) {
      health = "unknown";
      reason = "no sources or destinations";
    }

    const ingestFlows = collectionFlows.filter((f) => f.direction === "ingest");
    const itemsIngested = ingestFlows.length
      ? ingestFlows.reduce((sum, f) => sum + readFlowStats(f.flow_stats).items, 0)
      : null;

    const nodeHealth = (id: string): LineageHealth => {
      const connId = id.startsWith("conn:") ? id.slice(5) : null;
      if (!connId) return "unknown";
      const node = nodesById.get(id);
      const alert = alerts.find((a) => a.connection_id === connId);
      return worse(
        connectionHealth(node?.meta.status),
        alert ? alertHealth(alert) : "unknown",
      );
    };

    const lineage: LineageGroup[] = [
      {
        kind: "connection",
        label: "Sources",
        labelOne: "Source",
        href: "/connections",
        nodes: sources.map((e) => ({
          id: e.id,
          label: nodesById.get(e.from)?.label ?? e.from,
          health: nodeHealth(e.from),
          href: "/connections",
        })),
      },
      {
        kind: "process",
        label: "Processes",
        labelOne: "Process",
        href: "/processes",
        nodes: processes.map((e) => {
          const id = e.kind === "process_source" ? e.to : e.from;
          const node = nodesById.get(id);
          // Health stays "unknown": process alerts carry no anchor the client
          // can read (see ProductRollup.unattributed). Deployment state is the
          // one real signal the graph does return.
          const undeployed = node?.meta.deployed === false;
          return {
            id: `${e.kind}:${e.id}`,
            label: node?.label ?? id,
            detail: undeployed
              ? "not deployed"
              : e.kind === "process_source"
                ? "reads this product"
                : "writes this product",
            href: `/processes/${id.replace(/^proc:/, "")}`,
            health: undeployed ? ("warn" as const) : undefined,
          };
        }),
      },
      {
        kind: "connection",
        label: "Destinations",
        labelOne: "Destination",
        href: "/connections",
        nodes: destinations.map((e) => ({
          id: e.id,
          label: nodesById.get(e.to)?.label ?? e.to,
          health: nodeHealth(e.to),
          href: "/connections",
        })),
      },
    ];
    for (const group of lineage) {
      group.health = group.nodes.reduce<LineageHealth>(
        (acc, n) => worse(acc, n.health ?? "unknown"),
        group.nodes.length ? "ok" : "unknown",
      );
    }

    return {
      id: collection.id,
      title: collection.title ?? collection.id,
      itemsIngested,
      health,
      reason,
      lineage,
      onPlatform,
    };
  });

  return { rows, unattributed: alerts.filter((a) => !claimed.has(a.id)) };
}

/** Platform-wide counts for the stat tiles. */
export function buildStats(
  graph: PipelineGraph | undefined,
  flows: Association[] | undefined,
  chips: ConnectionChip[],
) {
  const processNodes = (graph?.nodes ?? []).filter((n) => n.type === "process");
  return {
    connections: chips.length,
    connectionsDegraded: chips.filter((c) => c.health === "error").length,
    processes: processNodes.length,
    processesUndeployed: processNodes.filter((n) => n.meta.deployed === false).length,
    // NOT a 24h window: `flow_stats` is cumulative and no existing response
    // carries a 24h rollup. Showing the honest all-time total beats inventing
    // an endpoint for the mockup's "Ingest (24h)" tile.
    itemsIngested: (flows ?? [])
      .filter((f) => f.direction === "ingest")
      .reduce((sum, f) => sum + readFlowStats(f.flow_stats).items, 0),
  };
}

/**
 * 30-day success rate for one association, from `/api/monitoring/history`.
 *
 * What counts as success depends on direction, because the rollup counts
 * different things at each end: ingest lands ITEMS and counts `failed`
 * alongside them, while delivery counts `delivered` against `failed` + `dead`.
 * Returns null when the window holds no attempts at all — a flow that has done
 * nothing is not 100% healthy, it is unmeasured.
 */
export function successRate(
  direction: "ingest" | "deliver",
  days: DailyStats[] | undefined,
): number | null {
  if (!days?.length) return null;
  let ok = 0;
  let bad = 0;
  for (const day of days) {
    if (direction === "ingest") {
      ok += day.items;
      bad += day.failed;
    } else {
      ok += day.delivered;
      bad += day.failed + day.dead;
    }
  }
  const total = ok + bad;
  return total === 0 ? null : (ok / total) * 100;
}

/**
 * Open alerts carrying NO anchor the client can read — connection,
 * association, channel and collection all null. Today that is exactly the
 * process-anchored kinds (`process_stalled` / `process_failed` /
 * `process_rate_limited`): migration 024 stores `process_id`/`source_id`, but
 * `/api/alerts` does not return them, so a product page cannot tell whether
 * one of these belongs to it. Surfacing the count is the honest fallback.
 */
export function unanchoredAlerts(openAlerts: Alert[] | undefined): Alert[] {
  return (openAlerts ?? []).filter(
    (a) =>
      !a.connection_id && !a.association_id && !a.channel_id && !a.collection_id,
  );
}
