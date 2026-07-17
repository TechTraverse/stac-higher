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
  createAssociation,
  DuplicateAssociationError,
  listAssociations,
  toApiAssociation,
  updateAssociation,
} from "@/lib/associations/storage";

const mockQuery = vi.mocked(query);

const ASSOC_ID = "3a9f1c2e-0000-4000-8000-0000000000a1";

const dbRow = {
  id: ASSOC_ID,
  collection_id: "sentinel-2",
  connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
  direction: "ingest" as const,
  enabled: true,
  config: { source_path: "/out", poll_frequency_seconds: 300 },
  expectation: null,
  flow_stats: { files: 3 },
  created_by: "user-1",
  created_at: new Date("2026-06-01T00:00:00Z"),
  updated_at: new Date("2026-06-02T00:00:00Z"),
  connection_name: "SFTP drop",
  connection_protocol: "sftp" as const,
  connection_status: "ok" as const,
  connection_group_id: "earth-observation",
};

beforeEach(() => {
  mockQuery.mockReset();
  mockQuery.mockResolvedValue({ rows: [dbRow], rowCount: 1 } as never);
});

describe("listAssociations", () => {
  it("LEFT JOINs the connection and scopes by collection_id", async () => {
    await listAssociations("sentinel-2");
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/LEFT JOIN stac_higher\.connections/);
    expect(sql).toMatch(/WHERE cc\.collection_id = \$1/);
    expect(params).toEqual(["sentinel-2"]);
  });

  it("exposes the connection group id internally but strips it from the API shape", async () => {
    const [withGroup] = await listAssociations("sentinel-2");
    expect(withGroup.connectionGroupId).toBe("earth-observation");
    const api = toApiAssociation(withGroup);
    expect(api).not.toHaveProperty("connectionGroupId");
    expect(api.connection).toEqual({
      name: "SFTP drop",
      protocol: "sftp",
      status: "ok",
    });
    expect(api.flow_stats).toEqual({ files: 3 });
  });
});

describe("updateAssociation", () => {
  it("never writes flow_stats and always bumps updated_at", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [{ id: ASSOC_ID }], rowCount: 1 } as never)
      .mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never);
    await updateAssociation(ASSOC_ID, { enabled: false });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).not.toMatch(/flow_stats/);
    expect(sql).toMatch(/updated_at = now\(\)/);
    expect(sql).toMatch(/enabled = \$1/);
  });

  it("serializes config as JSON", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [{ id: ASSOC_ID }], rowCount: 1 } as never)
      .mockResolvedValueOnce({ rows: [dbRow], rowCount: 1 } as never);
    await updateAssociation(ASSOC_ID, {
      config: {
        source_path: "/out",
        include: [],
        exclude: [],
        poll_frequency_seconds: 300,
        storage_mode: "copy",
        grouping: { rule: "none", timeout_seconds: 900, on_timeout: "ingest_partial" },
        metadata: { strategy: "raster_auto", defaults: {} },
        post_ingest: "leave",
      },
    });
    const [, params] = mockQuery.mock.calls[0];
    expect(typeof (params as unknown[])[0]).toBe("string");
    expect((params as string[])[0]).toContain("source_path");
  });
});

describe("createAssociation", () => {
  it("maps a unique-violation (23505) to DuplicateAssociationError", async () => {
    mockQuery.mockReset();
    mockQuery.mockRejectedValueOnce({ code: "23505" });
    await expect(
      createAssociation({
        collectionId: "sentinel-2",
        connectionId: dbRow.connection_id,
        direction: "ingest",
        enabled: true,
        config: {
          source_path: "/out",
          include: [],
          exclude: [],
          poll_frequency_seconds: 300,
          storage_mode: "copy",
          grouping: { rule: "none", timeout_seconds: 900, on_timeout: "ingest_partial" },
          metadata: { strategy: "raster_auto", defaults: {} },
          post_ingest: "leave",
        },
        expectation: null,
        createdBy: "user-1",
      }),
    ).rejects.toBeInstanceOf(DuplicateAssociationError);
  });
});
