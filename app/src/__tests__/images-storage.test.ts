// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { query } from "@/lib/db/connection";
import { getImageForGate } from "@/lib/images/storage";

beforeEach(() => vi.mocked(query).mockReset());

describe("getImageForGate", () => {
  it("joins only LIVE connections, so a soft-deleted credential reads as no group", async () => {
    vi.mocked(query).mockResolvedValue({ rows: [] } as never);
    expect(await getImageForGate("img-1")).toBeNull();
    const [sql, params] = vi.mocked(query).mock.calls[0];
    expect(sql).toContain("FROM stac_higher.container_images i");
    expect(sql).toMatch(/LEFT JOIN stac_higher\.connections c\s+ON c\.id = i\.registry_connection_id AND c\.deleted_at IS NULL/);
    expect(params).toEqual(["img-1"]);
  });

  it("normalizes last_scanned_at to a Date", async () => {
    vi.mocked(query).mockResolvedValue({
      rows: [
        {
          id: "img-1", reference: "ghcr.io/x/y", digest: null, status: "pending",
          last_scanned_at: "2026-09-01T00:00:00.000Z", registry_connection_id: null,
          registry_connection_group_id: null,
        },
      ],
    } as never);
    const row = await getImageForGate("img-1");
    expect(row?.last_scanned_at).toEqual(new Date("2026-09-01T00:00:00.000Z"));
  });
});
