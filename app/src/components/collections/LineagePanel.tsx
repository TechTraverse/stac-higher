import { useMemo } from "react";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  lineage,
  LoadingState,
  PipelineDag,
} from "@stac-higher/shared";
import { FlowStrip } from "@/components/monitoring/FlowStrip";
import { useAlerts } from "@/lib/monitoring/queries";
import { ALERTS_PAGE_LIMIT } from "@/lib/monitoring/api";
import { usePipelineGraph, useFlowHistory } from "@/lib/monitoring/graph-queries";
import {
  degreeMap,
  makeDecorator,
  unhealthyNodeIds,
} from "@/lib/monitoring/graph-decorate";
import { collectionNode } from "@/lib/graph/edges";

/**
 * Collection lineage: what feeds this collection and what it feeds (M5-F,
 * redrawn in P-3).
 *
 * The picture is the SAME row the `/graph` Pipelines view draws — one
 * component, two surfaces, so the two can never drift (spec §5.1). It replaces
 * the two one-hop lists this panel used to show, which stopped at the
 * immediate neighbours: for `goes-geocolor` that meant the process and the
 * destination, and never the connection the data actually came from.
 *
 * Derived from `/api/monitoring/graph` rather than a bespoke endpoint — the
 * spec's own framing, and it means the panel and `/graph` can never disagree
 * about the wiring. The graph is already group-scoped server-side, so a
 * neighbour the caller cannot see simply is not there.
 *
 * The 30-day strips stay beneath, per ASSOCIATION/SOURCE (what
 * `flow_stats_daily` keys on), not per collection: a collection's health is the
 * health of the flows touching it, and averaging them would hide a broken one.
 */

function EdgeRow({
  label,
  direction,
  kind,
  subjectKind,
  subjectId,
}: {
  label: string;
  direction: "Upstream" | "Downstream";
  kind: string;
  subjectKind: "association" | "process";
  subjectId: string;
}) {
  const { data: history } = useFlowHistory(subjectKind, subjectId);
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-border p-3">
      <div className="min-w-0">
        <div className="truncate font-medium">{label}</div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge variant="outline">{kind.replace("_", " ")}</Badge>
          <span className="text-[11px] text-muted-foreground">{direction}</span>
        </div>
      </div>
      <FlowStrip
        days={history ?? []}
        metric={subjectKind === "process" ? "runs" : "items"}
        label={`30-day ${label} activity`}
      />
    </div>
  );
}

export function LineagePanel({ collectionId }: { collectionId: string }) {
  const { data: graph, isLoading } = usePipelineGraph();
  const {
    data: alerts,
    isLoading: alertsLoading,
    isError: alertsError,
  } = useAlerts("open");
  const node = collectionNode(collectionId);

  // "Healthy" is only claimed on a loaded, untruncated alert list — the same
  // evidence rule `overview.ts` / the dashboard use (I-84 fix-round-1).
  const alertsAreComplete =
    !alertsLoading && !alertsError && (alerts?.length ?? 0) < ALERTS_PAGE_LIMIT;

  const decorate = useMemo(
    () =>
      makeDecorator(
        unhealthyNodeIds(alerts ?? []),
        degreeMap(graph?.edges ?? []),
        alertsAreComplete,
      ),
    [alerts, graph?.edges, alertsAreComplete],
  );

  const row = useMemo(
    () => lineage(graph ?? { nodes: [], edges: [] }, node),
    [graph, node],
  );

  const flows = useMemo(() => {
    const labels = new Map((graph?.nodes ?? []).map((n) => [n.id, n.label]));
    const decorated = (graph?.edges ?? [])
      .filter((edge) => edge.to === node || edge.from === node)
      .map((edge) => {
        const inbound = edge.to === node;
        return {
          id: edge.id,
          kind: edge.kind,
          direction: (inbound ? "Upstream" : "Downstream") as
            | "Upstream"
            | "Downstream",
          label: labels.get(inbound ? edge.from : edge.to) ?? edge.from,
          // `flow_stats_daily` keys process edges on the SOURCE row and
          // association edges on the association row — both are `edge.id`.
          subjectKind: (edge.kind === "process_source"
            ? "process"
            : "association") as "association" | "process",
          // A process OUTPUT row has no flow_stats of its own (telemetry lives
          // on the source that triggered the run), so it gets no strip. An
          // extractor edge shares its association id with the ingest edge that
          // already carries the flow strip, so it must not draw a second one.
          hasHistory:
            edge.kind !== "process_output" && edge.kind !== "extractor",
        };
      });
    return decorated.filter((edge) => edge.hasHistory);
  }, [graph, node]);

  if (isLoading) return <LoadingState message="Loading lineage…" />;

  const wired = row.nodes.length > 1;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lineage</CardTitle>
        <CardDescription>
          This product&apos;s whole chain — back to the connection the data came
          from, forward to everything it feeds — with 30 days of activity per
          flow. Today is not shown: the daily rollup covers complete days only.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {wired ? (
          <PipelineDag
            graph={row}
            focus={node}
            decorate={decorate}
            label={`${collectionId} lineage`}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            Nothing is wired to this collection yet.
          </p>
        )}

        {flows.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
              Flow activity
            </h3>
            <div className="grid gap-2 lg:grid-cols-2">
              {flows.map((edge) => (
                <EdgeRow
                  key={`${edge.kind}:${edge.id}`}
                  label={edge.label}
                  direction={edge.direction}
                  kind={edge.kind}
                  subjectKind={edge.subjectKind}
                  subjectId={edge.id}
                />
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
