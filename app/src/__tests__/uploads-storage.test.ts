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
import { runMigrations } from "@/lib/db/migrate";
import {
  createStagedUpload,
  getStagedUpload,
  stagedUploadExpiresAt,
} from "@/lib/uploads/storage";
import { DEFAULT_STAGING_TTL_SECONDS } from "@/lib/uploads/config";

const mockQuery = vi.mocked(query);

const CREATED_AT = new Date("2026-08-30T10:00:00Z");

function dbRow(overrides: Record<string, unknown> = {}) {
  return {
    id: "0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060",
    collection_id: "sentinel-pushed",
    item_id: null,
    created_by: "user-1",
    group_id: "earth-observation",
    filenames: ["B04.tif", "B08.tif"],
    status: "pending" as const,
    result: null,
    error: null,
    created_at: CREATED_AT,
    finalized_at: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockQuery.mockReset();
});

describe("createStagedUpload (migration 020 ledger, spec §4.1)", () => {
  it("inserts the binding row and runs migrations first (route-local pattern)", async () => {
    mockQuery.mockResolvedValue({ rows: [dbRow()], rowCount: 1 } as never);

    const upload = await createStagedUpload({
      collectionId: "sentinel-pushed",
      createdBy: "user-1",
      groupId: "earth-observation",
      filenames: ["B04.tif", "B08.tif"],
    });

    expect(vi.mocked(runMigrations)).toHaveBeenCalled();
    expect(mockQuery).toHaveBeenCalledTimes(1);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("INSERT INTO stac_higher.staged_uploads");
    // The binding: {upload_id, collection_id, created_by, group_id,
    // filenames, status: 'pending'} — status comes from the column default.
    expect(sql).toMatch(
      /INSERT INTO stac_higher\.staged_uploads\s*\(id, collection_id, created_by, group_id, filenames\)/,
    );
    expect(params).toHaveLength(5);
    const [id, collectionId, createdBy, groupId, filenames] = params as string[];
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    expect(collectionId).toBe("sentinel-pushed");
    expect(createdBy).toBe("user-1");
    expect(groupId).toBe("earth-observation");
    expect(JSON.parse(filenames)).toEqual(["B04.tif", "B08.tif"]);

    expect(upload.status).toBe("pending");
    expect(upload.createdAt).toBe(CREATED_AT.toISOString());
  });

  it("derives expires_at from the ledger clock (created_at + STAGING_TTL)", async () => {
    mockQuery.mockResolvedValue({ rows: [dbRow()], rowCount: 1 } as never);
    const upload = await createStagedUpload({
      collectionId: "c",
      createdBy: "u",
      groupId: null,
      filenames: ["a.tif"],
    });
    expect(upload.expiresAt).toBe(
      new Date(
        CREATED_AT.getTime() + DEFAULT_STAGING_TTL_SECONDS * 1000,
      ).toISOString(),
    );
  });
});

describe("getStagedUpload", () => {
  it("returns null for an unknown id", async () => {
    mockQuery.mockResolvedValue({ rows: [], rowCount: 0 } as never);
    expect(
      await getStagedUpload("0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060"),
    ).toBeNull();
  });

  it("maps the row including pipeline-written verdict columns", async () => {
    const finalized = new Date("2026-08-30T10:05:00Z");
    mockQuery.mockResolvedValue({
      rows: [
        dbRow({
          item_id: "S2A_001",
          status: "rejected",
          result: { rejected: [{ reason: "missing_bytes" }] },
          error: "staged object B04.tif was never uploaded",
          finalized_at: finalized,
        }),
      ],
      rowCount: 1,
    } as never);

    const upload = await getStagedUpload("0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060");
    expect(upload).toMatchObject({
      itemId: "S2A_001",
      status: "rejected",
      result: { rejected: [{ reason: "missing_bytes" }] },
      error: "staged object B04.tif was never uploaded",
      finalizedAt: finalized.toISOString(),
    });
  });
});

describe("stagedUploadExpiresAt", () => {
  it("accepts a string timestamp too (pg may return either)", () => {
    expect(stagedUploadExpiresAt("2026-08-30T10:00:00.000Z")).toBe(
      new Date(
        CREATED_AT.getTime() + DEFAULT_STAGING_TTL_SECONDS * 1000,
      ).toISOString(),
    );
  });
});
