/**
 * Last-N runs as a bar per run: height is DURATION, colour is OUTCOME
 * (mockup 05, "last 20 runs · duration").
 *
 * Inline SVG rather than a charting dependency, for the same reason
 * `FlowStrip` is: this draws N rectangles, and the no-new-dependencies rule
 * bites hardest on the smallest components.
 *
 * Two honesty rules, both borrowed from `FlowStrip`:
 * - A run with no duration yet (queued, or running) is drawn at a minimum
 *   height in a neutral colour, so "in flight" never reads as "instant".
 * - Colour beats height: a dead run is red whatever its duration, because
 *   "did this break" is the question the strip exists to answer.
 */
import type { ProcessRun } from "@/lib/processes/types";
import { formatDuration, runDurationMs } from "./health";

const BAR = 5;
const GAP = 2;
const HEIGHT = 26;
const MIN_BAR = 3;

const FILL: Record<ProcessRun["status"], string> = {
  succeeded: "var(--color-success)",
  failed: "var(--color-warning)",
  dead: "var(--color-danger)",
  running: "var(--color-primary)",
  queued: "var(--color-muted-foreground)",
};

export function RunSparkline({
  runs,
  limit = 20,
}: {
  /** Newest first, as the runs endpoint returns them. */
  runs: ProcessRun[];
  limit?: number;
}) {
  // Oldest → newest, left to right, so the strip reads like a timeline.
  const window = runs.slice(0, limit).reverse();
  if (window.length === 0) {
    return (
      <span className="text-[11.5px] text-muted-foreground">No runs yet</span>
    );
  }

  const durations = window.map(runDurationMs);
  const peak = Math.max(...durations.map((d) => d ?? 0), 1);
  const width = window.length * (BAR + GAP);

  return (
    <div className="flex flex-col gap-1">
      <svg
        width={width}
        height={HEIGHT}
        viewBox={`0 0 ${width} ${HEIGHT}`}
        role="img"
        aria-label={`last ${window.length} runs by duration`}
        className="overflow-visible"
      >
        <title>{`last ${window.length} runs · duration`}</title>
        {window.map((run, i) => {
          const ms = durations[i];
          const h =
            ms === null
              ? MIN_BAR
              : Math.max(MIN_BAR, Math.round((ms / peak) * HEIGHT));
          return (
            <rect
              key={run.id}
              x={i * (BAR + GAP)}
              y={HEIGHT - h}
              width={BAR}
              height={h}
              rx={1}
              fill={FILL[run.status]}
              opacity={ms === null ? 0.5 : 1}
            >
              <title>{`${run.status} · ${formatDuration(ms)}`}</title>
            </rect>
          );
        })}
      </svg>
      <span className="text-[10.5px] text-muted-foreground">
        last {window.length} {window.length === 1 ? "run" : "runs"} · duration
      </span>
    </div>
  );
}
