import type { DailyStats } from "@/lib/monitoring/graph-api";

/**
 * A day-per-cell activity strip (M5-F, spec §10).
 *
 * Inline SVG rather than a charting dependency: this draws N rectangles, and
 * the no-new-dependencies rule applies to a 40-line component more than
 * anywhere.
 *
 * Two decisions that make it honest rather than decorative:
 *
 * - **The calendar is filled here, not in the API.** The history endpoint
 *   returns only the days it has rows for, so a MISSING day (the rollup did
 *   not run) stays distinguishable from a QUIET day (it ran, nothing
 *   happened). A quiet day is an empty cell; a missing one is hatched.
 * - **Failure beats volume.** A day with any failure is drawn as a failure
 *   even if it also moved a million items, because "did this flow break" is
 *   the question the strip exists to answer.
 */

const CELL = 10;
const GAP = 2;

export interface FlowStripProps {
  days: DailyStats[];
  /** Calendar length; cells with no row are drawn as "no data". */
  window?: number;
  /** Which counter carries "volume" for this subject. */
  metric?: keyof Omit<DailyStats, "day">;
  label?: string;
}

function isoDaysAgo(n: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - n);
  return date.toISOString().slice(0, 10);
}

export function FlowStrip({
  days,
  window = 30,
  metric = "items",
  label = "activity",
}: FlowStripProps) {
  const byDay = new Map(days.map((d) => [d.day, d]));
  // Oldest → newest, left to right, ending YESTERDAY: today's bucket is not
  // written yet (the rollup covers complete days only), so including it would
  // show every flow as newly dead each morning.
  const calendar = Array.from({ length: window }, (_, i) =>
    isoDaysAgo(window - i),
  );

  const volumes = calendar
    .map((day) => byDay.get(day)?.[metric] ?? 0)
    .filter((v) => v > 0);
  const peak = volumes.length ? Math.max(...volumes) : 0;

  const width = window * (CELL + GAP);
  return (
    <svg
      width={width}
      height={CELL}
      viewBox={`0 0 ${width} ${CELL}`}
      role="img"
      aria-label={`${window}-day ${label}`}
      className="overflow-visible"
    >
      <title>{`${window}-day ${label}`}</title>
      {calendar.map((day, index) => {
        const row = byDay.get(day);
        const x = index * (CELL + GAP);
        if (!row) {
          return (
            <rect
              key={day}
              x={x}
              y={0}
              width={CELL}
              height={CELL}
              rx={2}
              className="fill-muted/40"
            >
              <title>{`${day}: no data`}</title>
            </rect>
          );
        }
        const broke = row.failed > 0 || row.dead > 0;
        const volume = row[metric] ?? 0;
        // Opacity carries volume; hue carries health. A quiet-but-healthy day
        // still reads as "fine", not as "missing".
        const intensity = peak > 0 && volume > 0 ? 0.35 + 0.65 * (volume / peak) : 0.2;
        return (
          <rect
            key={day}
            x={x}
            y={0}
            width={CELL}
            height={CELL}
            rx={2}
            className={broke ? "fill-destructive" : "fill-primary"}
            opacity={broke ? 1 : intensity}
          >
            <title>
              {`${day}: ${volume} ${String(metric)}` +
                (broke ? `, ${row.failed + row.dead} failed` : "")}
            </title>
          </rect>
        );
      })}
    </svg>
  );
}
