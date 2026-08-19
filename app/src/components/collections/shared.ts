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

/**
 * The §5.1 expectation window as form-field text: "" when no expectation is
 * declared (M2-A — the field is optional; absence-of-data alerts only arm
 * against a declared window).
 */
export function expectationSecondsField(
  expectation: Record<string, unknown> | null,
  key: string,
): string {
  const value = expectation?.[key];
  return typeof value === "number" ? String(value) : "";
}

/**
 * Parse the expectation field back: "" → null (no expectation), a positive
 * integer → its value, anything else → undefined (invalid — caller toasts).
 */
export function parseExpectationSeconds(raw: string): number | null | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const value = Number(trimmed);
  if (!Number.isInteger(value) || value < 1) return undefined;
  return value;
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
