/** TanStack Query hooks for the pipeline graph + daily history (M5-F). */
import { useQuery } from "@tanstack/react-query";
import { graphKeys } from "@/lib/query/keys";
import type { SubjectKind } from "@/lib/monitoring/history";
import { fetchGraph, fetchHistory } from "./graph-api";

export function usePipelineGraph() {
  return useQuery({ queryKey: graphKeys.graph(), queryFn: fetchGraph });
}

export function useFlowHistory(
  subjectKind: SubjectKind,
  subjectId: string | null,
  days = 30,
) {
  return useQuery({
    queryKey: graphKeys.history(subjectKind, subjectId ?? "none", days),
    queryFn: () => fetchHistory(subjectKind, subjectId as string, days),
    enabled: !!subjectId,
    // History only changes once a day; refetching it on every focus would be
    // pure noise.
    staleTime: 60 * 60 * 1000,
  });
}
