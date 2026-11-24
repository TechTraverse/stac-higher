/**
 * The cube-server EDR `position` query (Z-7): a GET returning CSV with the
 * header `t,<variable>,longitude,latitude`. `t` is a naive UTC
 * `YYYY-MM-DD HH:MM:SS.nnnnnnnnn` that `Date` will not parse as is, an empty
 * value is a null step, and the `x-cube-missing-steps` header counts the steps
 * the server could not read (a NODD outage is a 200 with every value empty).
 */
import { cubeEdrPositionUrl } from "./urls";

export interface CubeSeriesPoint {
  /** Epoch ms. */
  t: number;
  value: number | null;
}

/** `2026-10-03 17:02:36.714359936` to `2026-10-03T17:02:36.714Z`; null when it is not that shape. */
export function edrTimeToIso(t: string): string | null {
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z?$/.exec(t.trim());
  if (!m) return null;
  const ms = (m[3] ?? "").slice(0, 3).padEnd(3, "0");
  return `${m[1]}T${m[2]}.${ms}Z`;
}

/** Parses the position CSV; the value column is found by header name. */
export function parseEdrPositionCsv(text: string, variable: string): CubeSeriesPoint[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (lines.length === 0) return [];
  const header = lines[0].split(",").map((h) => h.trim());
  const tCol = header.indexOf("t");
  const vCol = header.indexOf(variable);
  if (tCol < 0 || vCol < 0) return [];

  const points: CubeSeriesPoint[] = [];
  for (const line of lines.slice(1)) {
    const cells = line.split(",");
    const iso = edrTimeToIso(cells[tCol] ?? "");
    if (!iso) continue;
    const raw = (cells[vCol] ?? "").trim();
    const num = raw === "" ? NaN : Number(raw);
    points.push({ t: Date.parse(iso), value: Number.isFinite(num) ? num : null });
  }
  return points;
}

export interface CubePosition {
  points: CubeSeriesPoint[];
  /** The `x-cube-missing-steps` count; 0 when the header is absent. */
  missingSteps: number;
}

export async function fetchCubePosition(
  collectionId: string,
  lon: number,
  lat: number,
  variable: string,
): Promise<CubePosition> {
  // No credentials: the cube server is a different origin and needs none.
  const res = await fetch(cubeEdrPositionUrl(collectionId, lon, lat, variable), {
    credentials: "omit",
  });
  if (!res.ok) throw new Error(`cube position request failed: ${res.status}`);
  const missing = Number(res.headers.get("x-cube-missing-steps"));
  return {
    points: parseEdrPositionCsv(await res.text(), variable),
    missingSteps: Number.isFinite(missing) && missing > 0 ? missing : 0,
  };
}
