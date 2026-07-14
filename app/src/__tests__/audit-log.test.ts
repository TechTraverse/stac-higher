// @vitest-environment node
// (server-side db code — no DOM involved)
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

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
  listAuditEntries,
  sanitizeDetail,
  writeAudit,
} from "@/lib/audit/log";

const mockQuery = vi.mocked(query);

beforeEach(() => {
  mockQuery.mockReset();
  mockQuery.mockResolvedValue({ rows: [], rowCount: 0 } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("writeAudit", () => {
  it("inserts one append-only row with the expected columns", async () => {
    const ok = await writeAudit({
      actor: "user-1",
      actorGroups: ["earth-observation"],
      action: "create",
      resourceType: "extension",
      resourceId: "ext-1",
      detail: { outcome: "allowed", status: 201 },
    });
    expect(ok).toBe(true);
    expect(runMigrations).toHaveBeenCalled();
    expect(mockQuery).toHaveBeenCalledTimes(1);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/INSERT INTO stac_higher\.audit_log/);
    expect(sql).not.toMatch(/UPDATE|DELETE/i);
    expect(params).toEqual([
      "user-1",
      ["earth-observation"],
      "create",
      "extension",
      "ext-1",
      JSON.stringify({ outcome: "allowed", status: 201 }),
    ]);
  });

  it("never stores secrets: a credential-shaped payload is redacted", async () => {
    await writeAudit({
      actor: "user-1",
      actorGroups: [],
      action: "create",
      resourceType: "connection",
      detail: {
        host: "sftp.example.com",
        password: "hunter2",
        client_secret: "kc-secret-value",
        apiKey: "AKIAIOSFODNN7EXAMPLE",
        nested: { credentials: { user: "u" }, sshPrivateKey: "-----BEGIN KEY-----" },
        // secret-shaped VALUES under innocent keys
        note: "Bearer abc.def.ghi",
        blob: "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln",
      },
    });
    const stored = mockQuery.mock.calls[0][1]![5] as string;
    expect(stored).not.toContain("hunter2");
    expect(stored).not.toContain("kc-secret-value");
    expect(stored).not.toContain("AKIAIOSFODNN7EXAMPLE");
    expect(stored).not.toContain("Bearer abc.def.ghi");
    expect(stored).not.toContain("eyJhbGciOiJSUzI1NiJ9");
    expect(stored).not.toContain("-----BEGIN");
    const parsed = JSON.parse(stored);
    expect(parsed.host).toBe("sftp.example.com");
    expect(parsed.password).toBe("[REDACTED]");
    expect(parsed.nested.credentials).toBe("[REDACTED]");
    expect(parsed.note).toBe("[REDACTED]");
  });

  it("resolves false (never throws) when the insert fails, and logs loudly", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => {});
    mockQuery.mockRejectedValueOnce(new Error("connection refused"));
    const ok = await writeAudit({
      actor: "user-1",
      actorGroups: [],
      action: "delete",
      resourceType: "extension",
      resourceId: "ext-1",
    });
    expect(ok).toBe(false);
    expect(consoleError).toHaveBeenCalledWith(
      expect.stringContaining("[audit] FAILED"),
    );
  });

  it("resolves false when migrations fail (audit must not 500 the request)", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.mocked(runMigrations).mockRejectedValueOnce(new Error("db down"));
    const ok = await writeAudit({
      actor: "user-1",
      actorGroups: [],
      action: "login",
      resourceType: "session",
    });
    expect(ok).toBe(false);
    expect(mockQuery).not.toHaveBeenCalled();
  });
});

describe("sanitizeDetail", () => {
  it("caps recursion depth", () => {
    // deeper than MAX_DEPTH (6)
    const deep = { a: { b: { c: { d: { e: { f: { g: { h: "value" } } } } } } } };
    const out = JSON.stringify(sanitizeDetail(deep));
    expect(out).toContain("[TRUNCATED]");
    expect(out).not.toContain('"value"');
  });

  it("passes ordinary values through untouched", () => {
    const detail = { method: "POST", status: 201, tags: ["a", "b"], on: true };
    expect(sanitizeDetail(detail)).toEqual(detail);
  });
});

describe("listAuditEntries", () => {
  const row = {
    id: "42",
    actor: "user-1",
    actor_groups: ["earth-observation"],
    action: "create",
    resource_type: "extension",
    resource_id: "ext-1",
    detail: { outcome: "allowed" },
    at: new Date("2026-07-14T12:00:00Z"),
  };

  it("filters by group overlap for operators", async () => {
    mockQuery.mockResolvedValueOnce({ rows: [row], rowCount: 1 } as never);
    const result = await listAuditEntries({
      groups: ["earth-observation"],
      limit: 10,
    });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/actor_groups && \$1::text\[\]/);
    expect(params![0]).toEqual(["earth-observation"]);
    expect(result.entries).toEqual([
      {
        id: "42",
        actor: "user-1",
        actorGroups: ["earth-observation"],
        action: "create",
        resourceType: "extension",
        resourceId: "ext-1",
        detail: { outcome: "allowed" },
        at: "2026-07-14T12:00:00.000Z",
      },
    ]);
    expect(result.nextCursor).toBeNull();
  });

  it("applies no group filter for admins (groups: null)", async () => {
    await listAuditEntries({ groups: null });
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).not.toMatch(/actor_groups &&/);
  });

  it("paginates with an exclusive id cursor and reports nextCursor", async () => {
    const rows = [
      { ...row, id: "30" },
      { ...row, id: "29" },
      { ...row, id: "28" }, // limit+1 sentinel
    ];
    mockQuery.mockResolvedValueOnce({ rows, rowCount: 3 } as never);
    const result = await listAuditEntries({
      groups: null,
      limit: 2,
      before: "31",
    });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/id < \$1::bigint/);
    expect(sql).toMatch(/ORDER BY id DESC/);
    expect(params).toEqual(["31", 3]); // limit + 1
    expect(result.entries).toHaveLength(2);
    expect(result.nextCursor).toBe("29");
  });
});
