// @vitest-environment node
// (server-side db code — no DOM involved)
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import { query } from "@/lib/db/connection";
import {
  ackAlert,
  listAlerts,
  resolveAlert,
} from "@/lib/alerts/storage";

const mockQuery = vi.mocked(query);

const ALERT_ID = "3a9f1c2e-0000-4000-8000-0000000000e1";

const dbRow = {
  id: ALERT_ID,
  source: "health" as const,
  kind: "connection_error",
  connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
  association_id: null,
  channel_id: null,
  state: "firing" as const,
  message: "connection 'src' failing health checks: boom",
  first_seen: new Date("2026-08-18T00:00:00Z"),
  last_seen: new Date("2026-08-18T01:00:00Z"),
  acknowledged_at: null,
  acknowledged_by: null,
  resolved_at: null,
  group_id: "g1",
  connection_name: "src",
  collection_id: null,
  process_id: "3a9f1c2e-0000-4000-8000-0000000000p1",
  source_id: "3a9f1c2e-0000-4000-8000-0000000000s1",
};

beforeEach(() => {
  mockQuery.mockReset();
});

describe("listAlerts", () => {
  it("scopes to the caller's groups and maps timestamps to ISO strings", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never);

    const alerts = await listAlerts(["g1", "g2"]);

    expect(alerts).toHaveLength(1);
    expect(alerts[0].last_seen).toBe("2026-08-18T01:00:00.000Z");
    expect(alerts[0].group_id).toBe("g1");

    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(
      /COALESCE\(c\.group_id, nch\.group_id, cs\.group_id, pr\.group_id\) = ANY\(\$1::text\[\]\)/,
    );
    // Group derivation goes through the association's connection too.
    expect(sql).toMatch(/COALESCE\(a\.connection_id, cc\.connection_id\)/);
    // Collection-anchored alerts (P7-H) derive their group from the
    // collection's settings row; the display collection prefers the anchor.
    expect(sql).toMatch(/collection_settings cs\s+ON cs\.collection_id = a\.collection_id/);
    expect(sql).toMatch(/COALESCE\(a\.collection_id, cc\.collection_id\) AS collection_id/);
    expect(sql).toMatch(/ORDER BY \(a\.state = 'firing'\) DESC, a\.last_seen DESC/);
    expect(params).toEqual([["g1", "g2"], 50]);
  });

  it("admin (null groups) sees everything, unfiltered", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    await listAlerts(null);
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).not.toMatch(/group_id = ANY/);
  });

  it("state 'open' filters out resolved; a single state filters exactly", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    await listAlerts(null, { state: "open" });
    expect(mockQuery.mock.calls[0][0]).toMatch(/a\.state <> 'resolved'/);

    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    await listAlerts(null, { state: "resolved", limit: 10 });
    const [sql, params] = mockQuery.mock.calls[1];
    expect(sql).toMatch(/a\.state = \$1/);
    expect(params).toEqual(["resolved", 10]);
  });

  it("returns the effective process and the raw source anchor (I-84)", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never);
    const [row] = await listAlerts(["g1"]);
    expect(row.process_id).toBe("3a9f1c2e-0000-4000-8000-0000000000p1");
    expect(row.source_id).toBe("3a9f1c2e-0000-4000-8000-0000000000s1");
    // The projection COALESCEs the alert's own process with its source's
    // parent — the mapper must not re-derive it.
    const sql = mockQuery.mock.calls[0][0] as string;
    expect(sql).toMatch(/COALESCE\(a\.process_id, ps\.process_id\) AS process_id/);
    // Anchored on the projection (not the pre-existing `ps` join predicate,
    // which also contains the literal text "a.source_id") so this fails if
    // the SELECT list's copy of source_id is ever removed.
    expect(sql).toMatch(/AS process_id,\s+a\.source_id\s+FROM/);
  });
});

describe("ackAlert", () => {
  it("only acknowledges a firing row (state-guarded) and stamps the actor", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [{ id: ALERT_ID }], rowCount: 1 } as never)
      // the re-read through getAlert
      .mockResolvedValueOnce({
        rows: [{ ...dbRow, state: "acknowledged", acknowledged_by: "alice" }],
        rowCount: 1,
      } as never);

    const result = await ackAlert(ALERT_ID, "alice");

    expect(result?.state).toBe("acknowledged");
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/SET state = 'acknowledged'/);
    expect(sql).toMatch(/AND state = 'firing'/);
    expect(params).toEqual([ALERT_ID, "alice"]);
  });

  it("returns null when the row is not firing", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    expect(await ackAlert(ALERT_ID, "alice")).toBeNull();
  });
});

describe("resolveAlert", () => {
  it("resolves from either open state, guarded against double-resolve", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [{ id: ALERT_ID }], rowCount: 1 } as never)
      .mockResolvedValueOnce({
        rows: [{ ...dbRow, state: "resolved" }],
        rowCount: 1,
      } as never);

    const result = await resolveAlert(ALERT_ID);

    expect(result?.state).toBe("resolved");
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/SET state = 'resolved', resolved_at = now\(\)/);
    expect(sql).toMatch(/AND state <> 'resolved'/);
  });

  it("returns null when already resolved", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [], rowCount: 0 } as never);
    expect(await resolveAlert(ALERT_ID)).toBeNull();
  });
});
