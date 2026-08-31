/**
 * Client functions for the pipeline graph and its daily history (M5-F).
 *
 * Same-origin JSON. Both endpoints are member+ scoped server-side, so the
 * client never filters for visibility — what comes back is what the caller
 * may see.
 */
import type { GraphEdge } from "@/lib/graph/edges";
import type { SubjectKind } from "@/lib/monitoring/history";

export interface GraphNode {
  id: string;
  type: "collection" | "process" | "connection";
  label: string;
  group_id: string | null;
  meta: Record<string, unknown>;
}

export interface PipelineGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface DailyStats {
  day: string;
  files: number;
  items: number;
  bytes: number;
  delivered: number;
  failed: number;
  dead: number;
  runs: number;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    throw new Error(
      (typeof body.error === "string" && body.error) ||
        `Request failed: ${res.status}`,
    );
  }
  return res.json() as Promise<T>;
}

export function fetchGraph(): Promise<PipelineGraph> {
  return getJson<PipelineGraph>("/api/monitoring/graph");
}

export async function fetchHistory(
  subjectKind: SubjectKind,
  subjectId: string,
  days = 30,
): Promise<DailyStats[]> {
  const params = new URLSearchParams({
    subject_kind: subjectKind,
    subject_id: subjectId,
    days: String(days),
  });
  const data = await getJson<{ days: DailyStats[] }>(
    `/api/monitoring/history?${params}`,
  );
  return data.days;
}
