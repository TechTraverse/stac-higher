import { useQuery } from "@tanstack/react-query";
import { authKeys } from "@/lib/query/keys";
import type { AuthContext } from "@/lib/auth/types";

async function fetchAuthMe(): Promise<AuthContext> {
  const res = await fetch("/api/auth/me");
  if (!res.ok) {
    throw new Error(`Failed to load auth context (HTTP ${res.status})`);
  }
  return res.json();
}

/** The current request's canonical auth context (identity only — never tokens). */
export function useAuthMe() {
  return useQuery({
    queryKey: authKeys.me(),
    queryFn: fetchAuthMe,
    staleTime: 60_000,
    retry: false,
  });
}
