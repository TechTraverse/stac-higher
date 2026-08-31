import { useMemo } from "react";
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  LoadingState,
} from "@stac-higher/shared";
import { ArrowRight } from "lucide-react";
import { FlowStrip } from "@/components/monitoring/FlowStrip";
import { usePipelineGraph, useFlowHistory } from "@/lib/monitoring/graph-queries";
import { collectionNode } from "@/lib/graph/edges";

/**
 * Collection lineage: what feeds this collection and what it feeds (M5-F).
 *
 * Derived from `/api/monitoring/graph` rather than a bespoke endpoint — the
 * spec's own framing, and it means the panel and the `/graph` view can never
 * disagree about the wiring. The graph is already group-scoped server-side,
 * so a neighbour the caller cannot see simply is not there.
 *
 * The 30-day strip is per ASSOCIATION/SOURCE (what `flow_stats_daily` keys
 * on), not per collection: a collection's health is the health of the flows
 * touching it, and averaging them would hide a broken one.
 */

function EdgeRow({
  label,
  kind,
  subjectKind,
  subjectId,
}: {
  label: string;
  kind: string;
  subjectKind: "association" | "process";
  subjectId: string;
}) {
  const { data: history } = useFlowHistory(subjectKind, subjectId);
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border border-border p-3">
      <div className="min-w-0">
        <div className="font-medium truncate">{label}</div>
        <Badge variant="outline">{kind.replace("_", " ")}</Badge>
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
  const node = collectionNode(collectionId);

  const { upstream, downstream } = useMemo(() => {
    const labels = new Map((graph?.nodes ?? []).map((n) => [n.id, n.label]));
    const inbound = (graph?.edges ?? []).filter((e) => e.to === node);
    const outbound = (graph?.edges ?? []).filter((e) => e.from === node);
    const decorate = (edges: typeof inbound, otherEnd: "from" | "to") =>
      edges.map((edge) => ({
        id: edge.id,
        kind: edge.kind,
        label: labels.get(edge[otherEnd]) ?? edge[otherEnd],
        // `flow_stats_daily` keys process edges on the SOURCE row and
        // association edges on the association row — both are `edge.id`.
        subjectKind: (edge.kind === "process_source" ? "process" : "association") as
          | "association"
          | "process",
        // A process OUTPUT row has no flow_stats of its own (telemetry lives
        // on the source that triggered the run), so it gets no strip.
        hasHistory: edge.kind !== "process_output",
      }));
    return {
      upstream: decorate(inbound, "from"),
      downstream: decorate(outbound, "to"),
    };
  }, [graph, node]);

  if (isLoading) return <LoadingState message="Loading lineage…" />;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lineage</CardTitle>
        <CardDescription>
          What feeds this collection and what it feeds, with 30 days of
          activity per flow. Today is not shown — the daily rollup covers
          complete days only.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            Upstream <ArrowRight className="h-4 w-4" />
          </h3>
          {upstream.length === 0 && (
            <p className="text-sm text-muted-foreground">
              Nothing feeds this collection.
            </p>
          )}
          {upstream.map((edge) =>
            edge.hasHistory ? (
              <EdgeRow
                key={edge.id}
                label={edge.label}
                kind={edge.kind}
                subjectKind={edge.subjectKind}
                subjectId={edge.id}
              />
            ) : (
              <div
                key={edge.id}
                className="flex items-center justify-between gap-4 rounded-md border border-border p-3"
              >
                <span className="font-medium truncate">{edge.label}</span>
                <Badge variant="outline">{edge.kind.replace("_", " ")}</Badge>
              </div>
            ),
          )}
        </div>
        <div className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium">
            <ArrowRight className="h-4 w-4" /> Downstream
          </h3>
          {downstream.length === 0 && (
            <p className="text-sm text-muted-foreground">
              This collection feeds nothing.
            </p>
          )}
          {downstream.map((edge) =>
            edge.hasHistory ? (
              <EdgeRow
                key={edge.id}
                label={edge.label}
                kind={edge.kind}
                subjectKind={edge.subjectKind}
                subjectId={edge.id}
              />
            ) : (
              <div
                key={edge.id}
                className="flex items-center justify-between gap-4 rounded-md border border-border p-3"
              >
                <span className="font-medium truncate">{edge.label}</span>
                <Badge variant="outline">{edge.kind.replace("_", " ")}</Badge>
              </div>
            ),
          )}
        </div>
      </CardContent>
    </Card>
  );
}
