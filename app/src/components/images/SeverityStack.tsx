/**
 * Severity counts as a compact stack with KEV called out (spec §9.2), read
 * from the stored verdict. "—" until a scan has produced one.
 */
import { Badge } from "@stac-higher/shared";
import type { ImageVerdict } from "@/lib/images/verdict";

const SEVERITIES = [
  ["critical", "C"],
  ["high", "H"],
  ["medium", "M"],
  ["low", "L"],
] as const;

export function SeverityStack({ verdict }: { verdict: ImageVerdict | null }) {
  if (!verdict?.counts) return <span className="text-muted-foreground">—</span>;
  const counts = verdict.counts;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs tabular-nums">
      {verdict.kev.length > 0 && <Badge variant="destructive">KEV {verdict.kev.length}</Badge>}
      {SEVERITIES.map(([key, short]) => {
        const n = counts[key] ?? 0;
        return (
          <span
            key={key}
            title={key}
            className={n > 0 && key === "critical" ? "font-semibold text-danger" : undefined}
          >
            {short} {n}
          </span>
        );
      })}
    </span>
  );
}
