// @vitest-environment node
/** M2-C channel storage: secret redaction + read-state watermark SQL. */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/db/connection", () => ({
  query: vi.fn(),
  getClient: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import { query } from "@/lib/db/connection";
import { listChannels } from "@/lib/notifications/storage";
import { countUnreadAlerts, markAlertsRead } from "@/lib/alerts/storage";

const mockQuery = vi.mocked(query);

const channelRow = {
  id: "3a9f1c2e-0000-4000-8000-0000000000c1",
  group_id: "earth-observation",
  kind: "webhook" as const,
  config: { url: "https://hooks.example.com/stac", secret: "s3cret" },
  created_by: "user-1",
  created_at: new Date("2026-08-18T00:00:00Z"),
  updated_at: new Date("2026-08-18T00:00:00Z"),
};

beforeEach(() => {
  mockQuery.mockReset();
});

describe("listChannels", () => {
  it("redacts the webhook secret to has_secret", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [channelRow], rowCount: 1 } as never);
    const [ch] = await listChannels(["earth-observation"]);
    expect(ch.config).toEqual({
      url: "https://hooks.example.com/stac",
      has_secret: true,
    });
    expect(JSON.stringify(ch)).not.toContain("s3cret");
  });

  it("in_app config redacts to an empty object", async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [{ ...channelRow, kind: "in_app" as const, config: {} }],
      rowCount: 1,
    } as never);
    const [ch] = await listChannels(null);
    expect(ch.config).toEqual({});
    // admin scope: no group filter parameter
    expect(mockQuery.mock.calls[0][1]).toEqual([]);
  });
});

describe("read state", () => {
  it("countUnreadAlerts filters firing-past-watermark within the caller's groups", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ count: "2" }], rowCount: 1 } as never);
    const count = await countUnreadAlerts("user-1", ["g1"]);
    expect(count).toBe(2);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("state = 'firing'");
    expect(sql).toContain("first_seen >");
    expect(sql).toContain("alert_reads");
    expect(params).toEqual(["user-1", ["g1"]]);
  });

  it("markAlertsRead upserts the watermark and returns it as ISO", async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [{ last_read_at: new Date("2026-08-19T00:00:00Z") }],
      rowCount: 1,
    } as never);
    const iso = await markAlertsRead("user-1");
    expect(iso).toBe("2026-08-19T00:00:00.000Z");
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("ON CONFLICT (user_sub)");
    expect(params).toEqual(["user-1"]);
  });
});
