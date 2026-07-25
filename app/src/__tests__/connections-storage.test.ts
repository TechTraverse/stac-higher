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
  createConnection,
  getConnectionCheck,
  hostKeyFingerprint,
  insertConnectionCheck,
  listConnections,
  resetHostKey,
  shouldClearHostKey,
  updateConnection,
} from "@/lib/connections/storage";

const mockQuery = vi.mocked(query);

const dbRow = {
  id: "3a9f1c2e-0000-4000-8000-000000000001",
  name: "SFTP drop",
  description: "",
  protocol: "sftp" as const,
  config: { host: "sftp.example.com", port: 22, root_path: "/" },
  credentials_set: true,
  host_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFoo comment",
  host_key_pinned_at: new Date("2026-07-01T00:00:00Z"),
  group_id: "earth-observation",
  created_by: "user-1",
  created_at: new Date("2026-06-01T00:00:00Z"),
  updated_at: new Date("2026-06-02T00:00:00Z"),
  enabled: true,
  status: "ok" as const,
  last_checked_at: new Date("2026-07-10T00:00:00Z"),
  last_error: null,
};

beforeEach(() => {
  mockQuery.mockReset();
  mockQuery.mockResolvedValue({ rows: [dbRow], rowCount: 1 } as never);
});

describe("credential invariants", () => {
  it("never SELECTs the credentials column — only IS NOT NULL presence", async () => {
    await listConnections(null);
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/credentials IS NOT NULL\) AS credentials_set/);
    // No bare "credentials" column reference outside the presence check.
    expect(
      (sql as string).replace(/\(credentials IS NOT NULL\) AS credentials_set/, ""),
    ).not.toMatch(/credentials/);
  });

  it("maps rows to the API shape without credential material", async () => {
    const [conn] = await listConnections(["earth-observation"]);
    expect(conn).not.toHaveProperty("credentials");
    expect(conn.credentials_set).toBe(true);
    expect(conn.host_key).toEqual({
      fingerprint: expect.stringMatching(/^SHA256:/),
      pinned_at: "2026-07-01T00:00:00.000Z",
    });
    expect(JSON.stringify(conn)).not.toContain("AAAAC3NzaC1lZDI1NTE5");
  });
});

describe("group scoping", () => {
  it("filters by group_id for non-admin callers", async () => {
    await listConnections(["weather", "earth-observation"]);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/WHERE deleted_at IS NULL AND group_id = ANY/);
    expect(params).toEqual([["weather", "earth-observation"]]);
  });

  it("applies only the not-deleted filter for admin (null)", async () => {
    await listConnections(null);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/WHERE deleted_at IS NULL/);
    expect(sql).not.toMatch(/group_id = ANY/);
    expect(params).toEqual([]);
  });
});

describe("updateConnection", () => {
  it("bumps updated_at app-side and resets status on config change", async () => {
    await updateConnection(dbRow.id, {
      config: { host: "new-host", port: 22, root_path: "/" },
    });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/updated_at = now\(\)/);
    expect(sql).toMatch(/status = 'unverified'/);
  });

  it("replaces the credential envelope wholesale when provided", async () => {
    const envelope = Buffer.from([0x01, 1, 2, 3]);
    await updateConnection(dbRow.id, { encryptedCredentials: envelope });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/credentials = \$\d/);
    expect(params).toContain(envelope);
    expect(sql).toMatch(/status = 'unverified'/);
  });

  it("keeps the health status on a pure metadata rename (still bumps updated_at)", async () => {
    await updateConnection(dbRow.id, { name: "Renamed" });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/updated_at = now\(\)/);
    expect(sql).not.toMatch(/status = 'unverified'/);
  });

  it("clears the host key when asked", async () => {
    await updateConnection(dbRow.id, {
      config: { host: "h2", port: 22, root_path: "/" },
      clearHostKey: true,
    });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/host_key = NULL/);
    expect(sql).toMatch(/host_key_pinned_at = NULL/);
  });
});

describe("shouldClearHostKey", () => {
  const oldConfig = { host: "a", port: 22, root_path: "/" };
  it("clears on ssh-family host or port change", () => {
    expect(shouldClearHostKey("sftp", oldConfig, { ...oldConfig, host: "b" })).toBe(true);
    expect(shouldClearHostKey("ssh", oldConfig, { ...oldConfig, port: 2222 })).toBe(true);
  });
  it("keeps the pin when endpoint is unchanged or protocol is not ssh-family", () => {
    expect(shouldClearHostKey("sftp", oldConfig, { ...oldConfig, root_path: "/x" })).toBe(false);
    expect(shouldClearHostKey("sftp", oldConfig, undefined)).toBe(false);
    expect(shouldClearHostKey("ftp", oldConfig, { ...oldConfig, host: "b" })).toBe(false);
  });
});

describe("resetHostKey", () => {
  it("clears the pin and drops status to unverified", async () => {
    await resetHostKey(dbRow.id);
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/host_key = NULL/);
    expect(sql).toMatch(/host_key_pinned_at = NULL/);
    expect(sql).toMatch(/status = 'unverified'/);
  });
});

describe("connection_checks bridge (ADR 0004)", () => {
  const checkRow = {
    id: "3a9f1c2e-0000-4000-8000-00000000000c",
    connection_id: dbRow.id,
    requested_by: "user-1",
    requested_at: new Date("2026-07-14T00:00:00Z"),
    status: "pending" as const,
    result: null,
    finished_at: null,
  };

  it("inserts a pending row for the pipeline drain job", async () => {
    mockQuery.mockResolvedValue({ rows: [checkRow], rowCount: 1 } as never);
    const check = await insertConnectionCheck(dbRow.id, "user-1");
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/INSERT INTO stac_higher\.connection_checks/);
    // Only connection_id + requested_by — status defaults to 'pending' in the DB.
    expect(params).toEqual([dbRow.id, "user-1"]);
    expect(check.status).toBe("pending");
  });

  it("strips the bridge-internal host_key from polled results", async () => {
    mockQuery.mockResolvedValue({
      rows: [
        {
          ...checkRow,
          status: "done",
          result: { ok: true, message: "connected", host_key: "ssh-ed25519 AAA", latency_ms: 42 },
          finished_at: new Date("2026-07-14T00:00:10Z"),
        },
      ],
      rowCount: 1,
    } as never);
    const check = await getConnectionCheck(dbRow.id, checkRow.id);
    expect(check?.result).toEqual({ ok: true, message: "connected", latency_ms: 42 });
  });
});

describe("createConnection", () => {
  it("stores the sealed envelope, never plaintext", async () => {
    const envelope = Buffer.from([0x01, 9, 9, 9]);
    await createConnection({
      name: "n",
      description: "",
      protocol: "sftp",
      config: { host: "h", port: 22, root_path: "/" },
      encryptedCredentials: envelope,
      groupId: "earth-observation",
      createdBy: "user-1",
      enabled: true,
    });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/INSERT INTO stac_higher\.connections/);
    expect(params).toContain(envelope);
  });
});

describe("hostKeyFingerprint", () => {
  it("produces an OpenSSH-style unpadded SHA256 fingerprint from the key blob", () => {
    const fp = hostKeyFingerprint("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFoo comment");
    expect(fp).toMatch(/^SHA256:[A-Za-z0-9+/]+$/);
    expect(fp).not.toMatch(/=$/);
    // Deterministic and independent of the comment.
    expect(hostKeyFingerprint("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFoo other")).toBe(fp);
  });
});
