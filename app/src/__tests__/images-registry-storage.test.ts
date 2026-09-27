// @vitest-environment node
/**
 * C-3 registry storage: pins the SQL shapes that carry a rule (the
 * connection join keeps soft-deleted rows, in-use counts CURRENT revisions
 * of LIVE processes, the scan poll survives the drain's dedup), and the
 * row -> API mapping. The DB is mocked; this is not an integration test.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/db/connection", () => ({ query: vi.fn(), getClient: vi.fn() }));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import { getClient, query } from "@/lib/db/connection";
import {
  IMAGE_LIST_LIMIT,
  findOpenAdmission,
  getImage,
  getImageScan,
  grantImageException,
  insertImageWithAdmission,
  isDigestLaunchable,
  listImageUsers,
  listImages,
  requestImageScan,
  revokeImage,
} from "@/lib/images/storage";

const mockQuery = vi.mocked(query);
const mockGetClient = vi.mocked(getClient);
const NOW = new Date("2026-09-27T12:00:00.000Z");
const VIEW = { now: NOW, scanWindowDays: 30 };
const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";
const DIGEST = "sha256:" + "a".repeat(64);

function imageRow(overrides: Record<string, unknown> = {}) {
  return {
    id: IMG,
    reference: "ghcr.io/org/img",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    verdict: { pass: true, reasons: [] },
    size_bytes: "812345678",
    config: { user: "", entrypoint: ["/entry.sh"], cmd: [] },
    last_scan_id: SCAN,
    last_scanned_at: new Date(NOW.getTime() - 86_400_000),
    added_by: "user-1",
    created_at: new Date("2026-09-20T00:00:00.000Z"),
    updated_at: new Date("2026-09-21T00:00:00.000Z"),
    exception_reason: null,
    exception_by: null,
    exception_at: null,
    exception_expires_at: null,
    tag_current_digest: null,
    tag_checked_at: null,
    registry_connection_id: null,
    registry_connection_name: null,
    registry_connection_group_id: null,
    registry_connection_deleted: null,
    db_built_at: "2026-09-20T06:00:00Z",
    in_use_by: 2,
    ...overrides,
  };
}

/** A pooled client whose `query` answers by SQL prefix. */
function mockClient(answer: (sql: string, params: unknown[]) => { rows: unknown[] }) {
  const client = {
    query: vi.fn(async (sql: string, params: unknown[] = []) => ({
      rowCount: 0,
      ...answer(sql, params),
    })),
    release: vi.fn(),
  };
  mockGetClient.mockResolvedValue(client as never);
  return client;
}

beforeEach(() => {
  mockQuery.mockReset();
  mockGetClient.mockReset();
});

describe("listImages / getImage", () => {
  it("maps a row: bigint size, computed staleness, drift, exception, in-use count", async () => {
    mockQuery.mockResolvedValue({
      rows: [
        imageRow({
          tag_current_digest: "sha256:" + "b".repeat(64),
          exception_reason: "vendor fix pending",
          exception_by: "admin-1",
          exception_at: new Date("2026-09-22T00:00:00.000Z"),
          exception_expires_at: new Date("2026-10-22T00:00:00.000Z"),
        }),
      ],
    } as never);
    const [image] = await listImages({}, VIEW);
    expect(image).toMatchObject({
      id: IMG,
      size_bytes: 812345678,
      stale: false,
      drifted: true,
      in_use_by: 2,
      db_built_at: "2026-09-20T06:00:00Z",
      registry_connection: null,
      exception: {
        reason: "vendor fix pending",
        by: "admin-1",
        at: "2026-09-22T00:00:00.000Z",
        expires_at: "2026-10-22T00:00:00.000Z",
      },
      created_at: "2026-09-20T00:00:00.000Z",
    });
  });

  it("keeps soft-deleted connections and flags them, so the UI never shows a public image", async () => {
    mockQuery.mockResolvedValue({
      rows: [
        imageRow({
          registry_connection_id: "c-1",
          registry_connection_name: "ghcr robot",
          registry_connection_group_id: "earth-observation",
          registry_connection_deleted: true,
        }),
      ],
    } as never);
    const [image] = await listImages({}, VIEW);
    expect(image.registry_connection).toEqual({
      id: "c-1",
      name: "ghcr robot",
      group_id: "earth-observation",
      deleted: true,
    });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/LEFT JOIN stac_higher\.connections c ON c\.id = i\.registry_connection_id\s/);
    expect(sql).not.toMatch(/c\.deleted_at IS NULL/);
  });

  it("counts only CURRENT revisions of LIVE processes as in use", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await listImages({}, VIEW);
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/ON p\.current_revision = r\.id AND p\.deleted_at IS NULL/);
    expect(sql).toMatch(/WHERE r\.runtime->'image'->>'id' = i\.id::text/);
  });

  it("applies filters as parameters and escapes LIKE wildcards in the search", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await listImages({ status: "flagged", q: "50%_off\\x", inUse: true }, VIEW);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = $1");
    expect(sql).toContain("ILIKE $2 ESCAPE '\\'");
    expect(sql).toContain("in_use_by > 0");
    expect(sql).toContain("LIMIT $3");
    expect(params).toEqual(["flagged", "%50\\%\\_off\\\\x%", IMAGE_LIST_LIMIT]);
  });

  it("filters to images nobody uses", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await listImages({ inUse: false }, VIEW);
    expect(mockQuery.mock.calls[0][0]).toContain("in_use_by = 0");
  });

  it("reports staleness as unknown when the policy window is unknown", async () => {
    mockQuery.mockResolvedValue({ rows: [imageRow()] } as never);
    const image = await getImage(IMG, { now: NOW, scanWindowDays: null });
    expect(image?.stale).toBeNull();
    expect(mockQuery.mock.calls[0][1]).toEqual([IMG]);
  });

  it("answers null for a missing image", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    expect(await getImage(IMG, VIEW)).toBeNull();
  });
});

describe("scans and users", () => {
  it("finds a scan by id even after the drain de-duplicated its provisional image", async () => {
    mockQuery.mockResolvedValue({ rows: [] } as never);
    await getImageScan(IMG, SCAN);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/s\.id = \$2/);
    expect(sql).toMatch(
      /s\.image_id = \$1\s+OR NOT EXISTS \(SELECT 1 FROM stac_higher\.container_images WHERE id = \$1\)/,
    );
    expect(params).toEqual([IMG, SCAN]);
  });

  it("lists the live processes whose current revision snapshots the image", async () => {
    mockQuery.mockResolvedValue({
      rows: [{ process_id: "p-1", name: "geocolor", group_id: "earth-observation" }],
    } as never);
    expect(await listImageUsers(IMG)).toEqual([
      { process_id: "p-1", name: "geocolor", group_id: "earth-observation" },
    ]);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/JOIN stac_higher\.process_revisions r ON r\.id = p\.current_revision/);
    expect(sql).toMatch(/p\.deleted_at IS NULL/);
    expect(params).toEqual([IMG]);
  });
});

describe("adding an image (ADR 0004: rows, never a call)", () => {
  it("takes the lock, re-checks on the same client, then inserts a pending image and its admission scan in one transaction", async () => {
    const client = mockClient((sql) => {
      if (sql.includes("INSERT INTO stac_higher.container_images")) return { rows: [{ id: IMG }] };
      if (sql.includes("INSERT INTO stac_higher.image_scans")) return { rows: [{ id: SCAN }] };
      return { rows: [] };
    });
    const added = await insertImageWithAdmission({
      reference: "docker.io/library/python",
      tag: "3.12-slim",
      registryConnectionId: null,
      addedBy: "user-1",
    });
    expect(added).toEqual({ image_id: IMG, scan_id: SCAN, deduplicated: false });
    const sqls = client.query.mock.calls.map(([sql]) => String(sql));
    const kinds = sqls.map((sql) => sql.trim().split(/\s+/)[0]);
    expect(kinds).toEqual(["BEGIN", "SELECT", "SELECT", "INSERT", "INSERT", "COMMIT"]);
    expect(sqls[1]).toContain("pg_advisory_xact_lock");
    expect(sqls[2]).toContain("FROM stac_higher.container_images i");
    expect(sqls[2]).toContain("s.status IN ('pending','running')");
    expect(client.query.mock.calls[1][1]).toEqual([
      "docker.io/library/python",
      "3.12-slim",
      null,
    ]);
    expect(client.query.mock.calls[3][1]).toEqual([
      "docker.io/library/python",
      "3.12-slim",
      null,
      "user-1",
    ]);
    expect(String(client.query.mock.calls[4][0])).toContain("'admission'");
    expect(client.release).toHaveBeenCalled();
  });

  it("returns the row the in-transaction re-check finds, deduplicated, and never inserts", async () => {
    const client = mockClient((sql) => {
      if (sql.startsWith("SELECT i.id AS image_id")) return { rows: [{ image_id: IMG, scan_id: SCAN }] };
      return { rows: [] };
    });
    const added = await insertImageWithAdmission({
      reference: "ghcr.io/org/img",
      tag: "1",
      registryConnectionId: null,
      addedBy: "user-1",
    });
    expect(added).toEqual({ image_id: IMG, scan_id: SCAN, deduplicated: true });
    const kinds = client.query.mock.calls.map(([sql]) => String(sql).trim().split(/\s+/)[0]);
    expect(kinds).toEqual(["BEGIN", "SELECT", "SELECT", "COMMIT"]);
    expect(client.query.mock.calls.some(([sql]) => String(sql).includes("INSERT"))).toBe(false);
    expect(client.release).toHaveBeenCalled();
  });

  it("rolls back and rethrows when the scan insert fails", async () => {
    const client = mockClient((sql) => {
      if (sql.includes("INSERT INTO stac_higher.container_images")) return { rows: [{ id: IMG }] };
      if (sql.includes("INSERT INTO stac_higher.image_scans")) throw new Error("boom");
      return { rows: [] };
    });
    await expect(
      insertImageWithAdmission({
        reference: "ghcr.io/org/img",
        tag: "1",
        registryConnectionId: null,
        addedBy: "user-1",
      }),
    ).rejects.toThrow("boom");
    expect(client.query.mock.calls.map(([sql]) => sql)).toContain("ROLLBACK");
    expect(client.release).toHaveBeenCalled();
  });

  it("finds an open admission for the same reference, tag and credential", async () => {
    mockQuery.mockResolvedValue({ rows: [{ image_id: IMG, scan_id: SCAN }] } as never);
    expect(
      await findOpenAdmission({ reference: "ghcr.io/org/img", tag: "1", registryConnectionId: null }),
    ).toEqual({ image_id: IMG, scan_id: SCAN, deduplicated: true });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("IS NOT DISTINCT FROM $3::uuid");
    expect(sql).toContain("s.status IN ('pending','running')");
    expect(params).toEqual(["ghcr.io/org/img", "1", null]);
  });
});

describe("requestImageScan", () => {
  function imageState(row: { status: string; sbom_ref: string | null } | null, openScan: string | null) {
    return mockClient((sql) => {
      if (sql.startsWith("SELECT status, sbom_ref")) return { rows: row ? [row] : [] };
      if (sql.startsWith("SELECT id FROM stac_higher.image_scans")) {
        return { rows: openScan ? [{ id: openScan }] : [] };
      }
      if (sql.startsWith("INSERT INTO stac_higher.image_scans")) return { rows: [{ id: SCAN }] };
      return { rows: [] };
    });
  }

  it("is not_found for a missing image", async () => {
    imageState(null, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({ outcome: "not_found" });
  });

  it("refuses a revoked image", async () => {
    imageState({ status: "revoked", sbom_ref: "scans/x/sbom.syft.json" }, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({ outcome: "revoked" });
  });

  it("reports the open scan instead of queueing a second one", async () => {
    imageState({ status: "approved", sbom_ref: "scans/x/sbom.syft.json" }, "open-1");
    expect(await requestImageScan(IMG, "user-1")).toEqual({
      outcome: "already_pending",
      scan_id: "open-1",
    });
  });

  it("queues a rescan when an SBOM is stored", async () => {
    const client = imageState({ status: "flagged", sbom_ref: "scans/x/sbom.syft.json" }, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({
      outcome: "requested",
      scan_id: SCAN,
      kind: "rescan",
    });
    const insert = client.query.mock.calls.find(([sql]) =>
      String(sql).startsWith("INSERT INTO stac_higher.image_scans"),
    );
    expect(insert?.[1]).toEqual([IMG, "rescan", "user-1"]);
    expect(client.query.mock.calls.some(([sql]) => String(sql).includes("SET status = 'pending'"))).toBe(false);
  });

  it("re-requests an ADMISSION (and resets scan_failed to pending) when no scan ever succeeded", async () => {
    const client = imageState({ status: "scan_failed", sbom_ref: null }, null);
    expect(await requestImageScan(IMG, "user-1")).toEqual({
      outcome: "requested",
      scan_id: SCAN,
      kind: "admission",
    });
    expect(
      client.query.mock.calls.some(([sql]) =>
        String(sql).includes("SET status = 'pending', updated_at = now()"),
      ),
    ).toBe(true);
  });
});

describe("human verdicts", () => {
  const EXPIRES = new Date("2026-10-27T00:00:00.000Z");

  it("grants an exception only from rejected or flagged", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ id: IMG }] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "granted" });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = 'approved'");
    expect(sql).toContain("status IN ('rejected','flagged')");
    expect(params).toEqual([IMG, "vendor fix pending", "admin-1", EXPIRES.toISOString()]);
  });

  it("says why an exception did not apply", async () => {
    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [{ status: "approved" }] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "wrong_status", status: "approved" });

    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [] } as never);
    expect(
      await grantImageException({ imageId: IMG, reason: "vendor fix pending", by: "admin-1", expiresAt: EXPIRES }),
    ).toEqual({ outcome: "not_found" });
  });

  it("revokes from any status except revoked, clearing a live exception", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ id: IMG }] } as never);
    expect(await revokeImage(IMG)).toEqual({ outcome: "revoked" });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toContain("status = 'revoked'");
    expect(sql).toContain("exception_expires_at = NULL");
    expect(sql).toContain("status <> 'revoked'");

    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [{ status: "revoked" }] } as never);
    expect(await revokeImage(IMG)).toEqual({ outcome: "already_revoked" });

    mockQuery
      .mockResolvedValueOnce({ rows: [] } as never)
      .mockResolvedValueOnce({ rows: [] } as never);
    expect(await revokeImage(IMG)).toEqual({ outcome: "not_found" });
  });
});

describe("isDigestLaunchable (the K-6 admission probe)", () => {
  it("asks for an approved-or-flagged, fresh row with that digest", async () => {
    mockQuery.mockResolvedValue({ rows: [{ ok: true }] } as never);
    expect(await isDigestLaunchable(DIGEST, 30)).toBe(true);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain("status IN ('approved','flagged')");
    expect(sql).toContain("last_scanned_at >= now() - make_interval(days => $2::int)");
    expect(params).toEqual([DIGEST, 30]);
  });
});
