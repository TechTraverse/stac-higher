/**
 * Small helpers shared by the Data-flow tab's ingest and delivery halves —
 * kept in one place so the two sections can't drift.
 */
import type { Connection } from "@/lib/connections/types";

/** Comma-separated input → trimmed, non-empty entries. */
export function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

/** Connection health → Badge variant (lowercase badges on association cards;
 * the /connections page renders its own capitalized labels). */
export const CONNECTION_STATUS_VARIANT: Record<
  NonNullable<Connection["status"]>,
  "default" | "secondary" | "destructive"
> = {
  ok: "default",
  unverified: "secondary",
  error: "destructive",
};
