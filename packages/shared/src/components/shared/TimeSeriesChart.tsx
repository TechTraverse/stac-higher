export interface TimeSeriesPoint {
  /** Epoch ms. */
  t: number;
  /** null is a gap, never zero. */
  value: number | null;
}

export interface TimeSeriesChartProps {
  /** Any order; sorted by `t` inside. */
  points: TimeSeriesPoint[];
  ariaLabel: string;
  /** Shown on the y labels, e.g. "K". */
  unit?: string;
  /** viewBox width; the svg itself is width 100%. */
  width?: number;
  height?: number;
  /** Default "HH:MM UTC". */
  formatTime?: (ms: number) => string;
  emptyMessage?: string;
}

const defaultFormatTime = (ms: number) => `${new Date(ms).toISOString().slice(11, 16)} UTC`;

const PAD = { left: 56, right: 12, top: 10, bottom: 24 };

/**
 * A plain-SVG line chart for one series. Null values are gaps: runs of
 * consecutive values are separate paths, and an isolated value (a run of one)
 * is a circle so it is never dropped. No chart dependency.
 */
export function TimeSeriesChart({
  points,
  ariaLabel,
  unit,
  width = 480,
  height = 160,
  formatTime = defaultFormatTime,
  emptyMessage = "No values at this point",
}: TimeSeriesChartProps) {
  const sorted = [...points].sort((a, b) => a.t - b.t);
  const present = sorted.filter((p): p is { t: number; value: number } => p.value !== null);

  if (present.length === 0) {
    return <p className="text-sm text-muted-foreground">{emptyMessage}</p>;
  }

  const min = Math.min(...present.map((p) => p.value));
  const max = Math.max(...present.map((p) => p.value));
  const t0 = sorted[0].t;
  const t1 = sorted[sorted.length - 1].t;
  const innerW = width - PAD.left - PAD.right;
  const innerH = height - PAD.top - PAD.bottom;

  // A flat series or a single timestamp would divide by zero: centre it.
  const x = (t: number) => PAD.left + (t1 === t0 ? innerW / 2 : ((t - t0) / (t1 - t0)) * innerW);
  const y = (v: number) => PAD.top + (max === min ? innerH / 2 : (1 - (v - min) / (max - min)) * innerH);

  const runs: { t: number; value: number }[][] = [];
  let run: { t: number; value: number }[] = [];
  for (const p of sorted) {
    if (p.value === null) {
      if (run.length) runs.push(run);
      run = [];
    } else {
      run.push({ t: p.t, value: p.value });
    }
  }
  if (run.length) runs.push(run);

  const fmt = (v: number) => `${v.toFixed(1)}${unit ? ` ${unit}` : ""}`;

  return (
    <svg
      role="img"
      aria-label={ariaLabel}
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      className="text-primary"
    >
      <g className="text-muted-foreground" fill="currentColor" fontSize="10">
        <text x={PAD.left - 6} y={PAD.top + 4} textAnchor="end">
          {fmt(max)}
        </text>
        <text x={PAD.left - 6} y={PAD.top + innerH} textAnchor="end">
          {fmt(min)}
        </text>
        <text x={PAD.left} y={height - 6} textAnchor="start">
          {formatTime(t0)}
        </text>
        <text x={width - PAD.right} y={height - 6} textAnchor="end">
          {formatTime(t1)}
        </text>
      </g>
      <line
        x1={PAD.left}
        x2={width - PAD.right}
        y1={PAD.top + innerH}
        y2={PAD.top + innerH}
        stroke="currentColor"
        className="text-border"
      />
      {runs.map((r, i) =>
        r.length === 1 ? (
          <circle
            key={i}
            data-testid="series-point"
            cx={x(r[0].t)}
            cy={y(r[0].value)}
            r={2.5}
            fill="currentColor"
          />
        ) : (
          <path
            key={i}
            data-testid="series-segment"
            d={r.map((p, j) => `${j === 0 ? "M" : "L"}${x(p.t).toFixed(2)} ${y(p.value).toFixed(2)}`).join(" ")}
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            strokeLinejoin="round"
          />
        ),
      )}
    </svg>
  );
}
