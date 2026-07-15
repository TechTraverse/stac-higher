import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  ConnectionApiError,
  createConnection,
  deleteConnection,
  listConnections,
  resetHostKey,
  runConnectionTest,
} from "@/lib/connections/api";
import type { ConnectionCreateInput } from "@/lib/connections/schemas";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

function jsonRes(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("connections api client", () => {
  it("listConnections unwraps the {connections} envelope", async () => {
    mockFetch.mockResolvedValueOnce(
      jsonRes(200, { connections: [{ id: "a" }, { id: "b" }] }),
    );
    const list = await listConnections();
    expect(list).toHaveLength(2);
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/connections");
    expect(opts.credentials).toBe("same-origin");
  });

  it("createConnection POSTs the body and returns the connection", async () => {
    const created = { id: "new", name: "S3" };
    mockFetch.mockResolvedValueOnce(jsonRes(201, created));
    const input = {
      protocol: "s3",
      name: "S3",
      description: "",
      group_id: "g1",
      enabled: true,
      config: { bucket: "b" },
      credentials: { access_key_id: "k", secret_access_key: "s" },
    } as unknown as ConnectionCreateInput;
    const result = await createConnection(input);
    expect(result).toEqual(created);
    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body).protocol).toBe("s3");
  });

  it("surfaces {error, code} as a ConnectionApiError with status + code", async () => {
    mockFetch.mockResolvedValue(
      jsonRes(403, { error: "forbidden here", code: "forbidden" }),
    );
    const err = await listConnections().catch((e) => e);
    expect(err).toBeInstanceOf(ConnectionApiError);
    expect(err).toMatchObject({
      message: "forbidden here",
      status: 403,
      code: "forbidden",
    });
  });

  it("deleteConnection tolerates a 204 (no body)", async () => {
    mockFetch.mockResolvedValueOnce(jsonRes(204, undefined));
    await expect(deleteConnection("x")).resolves.toBeUndefined();
  });

  it("resetHostKey POSTs to the reset route", async () => {
    mockFetch.mockResolvedValueOnce(jsonRes(200, { id: "x", host_key: null }));
    const conn = await resetHostKey("x");
    expect(conn).toMatchObject({ host_key: null });
    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/connections/x/host-key/reset");
    expect(opts.method).toBe("POST");
  });
});

describe("runConnectionTest poll flow", () => {
  it("polls until done and reports ok + latency", async () => {
    // POST /test -> pending; poll running -> done{ok}
    mockFetch
      .mockResolvedValueOnce(jsonRes(202, { check: { id: "c1", status: "pending" } }))
      .mockResolvedValueOnce(jsonRes(200, { check: { id: "c1", status: "running" } }))
      .mockResolvedValueOnce(
        jsonRes(200, {
          check: {
            id: "c1",
            status: "done",
            result: { ok: true, message: "reachable", latency_ms: 42 },
          },
        }),
      );
    const result = await runConnectionTest("conn1", {
      intervalMs: 1,
      maxTries: 10,
    });
    expect(result).toEqual({
      ok: true,
      message: "reachable",
      latencyMs: 42,
      timedOut: false,
    });
  });

  it("reports failure when the check settles as failed", async () => {
    mockFetch
      .mockResolvedValueOnce(jsonRes(202, { check: { id: "c2", status: "pending" } }))
      .mockResolvedValueOnce(
        jsonRes(200, {
          check: {
            id: "c2",
            status: "failed",
            result: { ok: false, message: "auth denied" },
          },
        }),
      );
    const result = await runConnectionTest("conn2", {
      intervalMs: 1,
      maxTries: 10,
    });
    expect(result.ok).toBe(false);
    expect(result.message).toBe("auth denied");
    expect(result.timedOut).toBe(false);
  });

  it("gives up after maxTries without looping forever", async () => {
    mockFetch.mockResolvedValue(
      jsonRes(200, { check: { id: "c3", status: "pending" } }),
    );
    // First call is the POST /test, then bounded polls.
    const result = await runConnectionTest("conn3", {
      intervalMs: 1,
      maxTries: 3,
    });
    expect(result.timedOut).toBe(true);
    expect(result.ok).toBe(false);
    // 1 POST + at most maxTries polls.
    expect(mockFetch.mock.calls.length).toBeLessThanOrEqual(1 + 3);
  });
});
