import { useQuery } from "@tanstack/react-query";
import { extractorKeys } from "@/lib/query/keys";
import { listBuiltinExtractors } from "./api";

/** The registry is a build-time constant of the server, so it never goes
 * stale within a session. */
export function useBuiltinExtractors() {
  return useQuery({
    queryKey: extractorKeys.builtin(),
    queryFn: listBuiltinExtractors,
    staleTime: Infinity,
  });
}
