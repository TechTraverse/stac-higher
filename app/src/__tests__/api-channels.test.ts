// @vitest-environment node
/**
 * /api/channels routes (M2-C): auth, group scoping, kind-aware config
 * validation, and secret redaction. Storage is mocked.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/notifications/storage", () => ({
  listChannels: vi.fn(),
  getChannel: vi.fn(),
  createChannel: vi.fn(),
  updateChannelConfig: vi.fn(),
  deleteChannel: vi.fn(),
}));
vi.mock("@/lib/db/migrate", () => ({
  runMigrations: vi.fn(async () => {}),
}));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import {
  createChannel,
  deleteChannel,
  getChannel,
  listChannels,
  updateChannelConfig,
  type ApiChannel,
} from "@/lib/notifications/storage";
import { GET as listRoute, POST as createRoute } from "@/pages/api/channels/index";
import {
  DELETE as deleteRoute,
  GET as getRoute,
  PUT as updateRoute,
} from "@/pages/api/channels/[id]";

const mockList = vi.mocked(listChannels);
const mockGet = vi.mocked(getChannel);
const mockCreate = vi.mocked(createChannel);
const mockUpdate = vi.mocked(updateChannelConfig);
const mockDelete = vi.mocked(deleteChannel);

const CHANNEL_ID = "3a9f1c2e-0000-4000-8000-0000000000c1";
const EO = "earth-observation";

function channel(overrides: Partial<ApiChannel> = {}): ApiChannel {
  return {
    id: CHANNEL_ID,
    group_id: EO,
    kind: "webhook",
    config: { url: "https://hooks.example.com/stac", has_secret: true },
    created_by: "user-1",
    created_at: "2026-08-18T00:00:00.000Z",
    updated_at: "2026-08-18T00:00:00.000Z",
    ...overrides,
  };
}

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}

const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };

type RouteHandler = (ctx: never) => Promise<Response> | Response;

function call(
  handler: RouteHandler,
  auth: AuthContext,
  { method = "GET", body }: { method?: string; body?: unknown } = {},
) {
  const url = new URL("http://localhost:4321/api/channels");
  return handler({
    url,
    locals: { auth },
    request: new Request(url, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
    params: { id: CHANNEL_ID },
  } as never);
}

beforeEach(() => {
  mockList.mockReset().mockResolvedValue([channel()]);
  mockGet.mockReset().mockResolvedValue(channel());
  mockCreate.mockReset().mockResolvedValue(channel());
  mockUpdate.mockReset().mockResolvedValue(channel({ config: { url: "https://new", has_secret: false } }));
  mockDelete.mockReset().mockResolvedValue(true);
});

describe("GET /api/channels", () => {
  it("401s an unauthenticated caller", async () => {
    expect((await call(listRoute, anon)).status).toBe(401);
  });

  it("scopes members to their groups; admins see all", async () => {
    expect((await call(listRoute, authed(["member"]))).status).toBe(200);
    expect(mockList).toHaveBeenCalledWith([EO]);
    await call(listRoute, authed(["admin"]));
    expect(mockList).toHaveBeenLastCalledWith(null);
  });
});

describe("POST /api/channels", () => {
  const webhookBody = {
    kind: "webhook",
    group_id: EO,
    config: { url: "https://hooks.example.com/stac", secret: "s3cret" },
  };

  it("401/403s non-operators", async () => {
    expect((await call(createRoute, anon, { method: "POST", body: webhookBody })).status).toBe(401);
    expect(
      (await call(createRoute, authed(["member"]), { method: "POST", body: webhookBody })).status,
    ).toBe(403);
  });

  it("creates a webhook channel in the caller's group", async () => {
    const res = await call(createRoute, authed(["operator"]), {
      method: "POST",
      body: webhookBody,
    });
    expect(res.status).toBe(201);
    expect(mockCreate).toHaveBeenCalledWith({
      groupId: EO,
      kind: "webhook",
      config: { url: "https://hooks.example.com/stac", secret: "s3cret" },
      createdBy: "user-1",
    });
  });

  it("403s an operator creating outside their groups", async () => {
    const res = await call(createRoute, authed(["operator"], ["other-group"]), {
      method: "POST",
      body: webhookBody,
    });
    expect(res.status).toBe(403);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("400s a webhook without a valid url and an in_app with config", async () => {
    for (const body of [
      { kind: "webhook", group_id: EO, config: {} },
      { kind: "webhook", group_id: EO, config: { url: "ftp://x" } },
      { kind: "in_app", group_id: EO, config: { url: "https://x" } },
    ]) {
      const res = await call(createRoute, authed(["operator"]), { method: "POST", body });
      expect(res.status).toBe(400);
    }
  });

  it("accepts an in_app channel with no config", async () => {
    mockCreate.mockResolvedValue(channel({ kind: "in_app", config: {} }));
    const res = await call(createRoute, authed(["operator"]), {
      method: "POST",
      body: { kind: "in_app", group_id: EO },
    });
    expect(res.status).toBe(201);
    expect(mockCreate).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "in_app", config: {} }),
    );
  });
});

describe("GET/PUT/DELETE /api/channels/[id]", () => {
  it("404s out-of-group rows for non-admins", async () => {
    mockGet.mockResolvedValue(channel({ group_id: "other-group" }));
    expect((await call(getRoute, authed(["member"]))).status).toBe(404);
    expect(
      (await call(updateRoute, authed(["operator"]), {
        method: "PUT",
        body: { config: { url: "https://new.example.com/h" } },
      })).status,
    ).toBe(404);
  });

  it("GET allows member+ of the owning group", async () => {
    const res = await call(getRoute, authed(["member"]));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.config).toEqual({ url: "https://hooks.example.com/stac", has_secret: true });
  });

  it("PUT requires operator and replaces config wholesale", async () => {
    expect(
      (await call(updateRoute, authed(["member"]), {
        method: "PUT",
        body: { config: { url: "https://new.example.com/h" } },
      })).status,
    ).toBe(403);
    const res = await call(updateRoute, authed(["operator"]), {
      method: "PUT",
      body: { config: { url: "https://new.example.com/h" } },
    });
    expect(res.status).toBe(200);
    expect(mockUpdate).toHaveBeenCalledWith(CHANNEL_ID, {
      url: "https://new.example.com/h",
    });
  });

  it("PUT re-validates the config against the row's kind", async () => {
    // The row is a webhook — an empty (in_app-shaped) config must not strip it.
    const res = await call(updateRoute, authed(["operator"]), {
      method: "PUT",
      body: { config: {} },
    });
    expect(res.status).toBe(400);
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("DELETE requires operator of the owning group", async () => {
    expect((await call(deleteRoute, authed(["member"]), { method: "DELETE" })).status).toBe(403);
    const res = await call(deleteRoute, authed(["operator"]), { method: "DELETE" });
    expect(res.status).toBe(200);
    expect(mockDelete).toHaveBeenCalledWith(CHANNEL_ID);
  });
});
