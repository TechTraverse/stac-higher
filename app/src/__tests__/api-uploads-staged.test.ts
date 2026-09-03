// @vitest-environment node
/**
 * Staged mode of POST /api/uploads + the GET /api/uploads/[uploadId] poll
 * (Phase 7 spec §4.1–4.2). The settings/ledger layer is mocked — an unmocked
 * DB lookup 500s with the Docker stack down (the M1-era api-assets lesson);
 * what's under test is the precondition set, the response contract, and the
 * poll route's auth + visibility scoping. Presigning stays real (offline).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/lib/collections/settings", () => ({
  getCollectionSettings: vi.fn(),
}));
vi.mock("@/lib/uploads/storage", () => ({
  createStagedUpload: vi.fn(),
  getStagedUpload: vi.fn(),
}));

import { getCollectionSettings } from "@/lib/collections/settings";
import type { CollectionSettings } from "@/lib/collections/settings";
import {
  createStagedUpload,
  getStagedUpload,
  type StagedUpload,
} from "@/lib/uploads/storage";
import { POST as mintRoute } from "@/pages/api/uploads/index";
import { GET as pollRoute } from "@/pages/api/uploads/[uploadId]";
import type { AuthContext, CanonicalRole } from "@/lib/auth/types";

const mockSettings = vi.mocked(getCollectionSettings);
const mockCreate = vi.mocked(createStagedUpload);
const mockGet = vi.mocked(getStagedUpload);

const UPLOAD_ID = "0d9c2f64-8f3a-4a5e-9b7d-1c2e3f405060";
const EO = "earth-observation";

function authed(roles: CanonicalRole[], groups: string[] = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}
const anonymous: AuthContext = { authenticated: false, mode: "oidc", identity: null };

function settings(overrides: Partial<CollectionSettings> = {}): CollectionSettings {
  return {
    collectionId: "sentinel-pushed",
    groupId: EO,
    externallyWritable: true,
    retentionDays: null,
    retentionMaxItems: null,
    gcGraceDays: 30,
    archived: false,
    servingEnabled: false,
    ...overrides,
  };
}

function session(overrides: Partial<StagedUpload> = {}): StagedUpload {
  return {
    id: UPLOAD_ID,
    collectionId: "sentinel-pushed",
    itemId: null,
    createdBy: "user-1",
    groupId: EO,
    filenames: ["B04.tif"],
    status: "pending",
    result: null,
    error: null,
    createdAt: "2026-08-30T10:00:00.000Z",
    expiresAt: "2026-08-31T10:00:00.000Z",
    finalizedAt: null,
    ...overrides,
  };
}

function mint(auth: AuthContext, body: unknown) {
  const url = new URL("http://localhost:4321/api/uploads");
  const request = new Request(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return mintRoute({ url, locals: { auth }, request, params: {} } as never);
}

function poll(auth: AuthContext, uploadId: string) {
  const url = new URL(`http://localhost:4321/api/uploads/${uploadId}`);
  return pollRoute({
    url,
    locals: { auth },
    request: new Request(url),
    params: { uploadId },
  } as never);
}

const stagedBody = {
  collection: "sentinel-pushed",
  files: [{ filename: "B04.tif", contentType: "image/tiff" }],
};

beforeEach(() => {
  mockSettings.mockReset();
  mockCreate.mockReset();
  mockGet.mockReset();
  mockSettings.mockResolvedValue(settings());
  mockCreate.mockResolvedValue(session());
});

describe("POST /api/uploads (staged mode — body without `item`)", () => {
  it("401s an anonymous caller", async () => {
    const res = await mint(anonymous, stagedBody);
    expect(res.status).toBe(401);
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("403s a member without the operator role", async () => {
    const res = await mint(authed(["member"]), stagedBody);
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
  });

  it("403s an operator outside the collection's owning group", async () => {
    const res = await mint(authed(["operator"], ["other-group"]), stagedBody);
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("lets any operator mint against an UNOWNED collection (ADR 0003)", async () => {
    mockSettings.mockResolvedValue(settings({ groupId: null }));
    mockCreate.mockResolvedValue(session({ groupId: null }));
    const res = await mint(authed(["operator"], ["other-group"]), stagedBody);
    expect(res.status).toBe(200);
  });

  it("admins bypass the group rule", async () => {
    const res = await mint(authed(["admin"], []), stagedBody);
    expect(res.status).toBe(200);
  });

  it("403s when externally_writable is off (missing settings row defaults false)", async () => {
    mockSettings.mockResolvedValue(settings({ externallyWritable: false }));
    const res = await mint(authed(["operator"]), stagedBody);
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("not_externally_writable");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("409s an archived collection (ADR 0011)", async () => {
    mockSettings.mockResolvedValue(settings({ archived: true }));
    const res = await mint(authed(["operator"]), stagedBody);
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("collection_archived");
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it("400s a path-traversal collection before touching the DB", async () => {
    const res = await mint(authed(["operator"]), { ...stagedBody, collection: ".." });
    expect(res.status).toBe(400);
    expect(mockSettings).not.toHaveBeenCalled();
  });

  it("400s filenames that collide after sanitizing", async () => {
    const res = await mint(authed(["operator"]), {
      collection: "sentinel-pushed",
      files: [{ filename: "B04 tif" }, { filename: "B04_tif" }],
    });
    expect(res.status).toBe(400);
    expect(mockSettings).not.toHaveBeenCalled();
  });

  it("mints the §4.1 response: upload_id, staging presigns, staged hrefs, ledger expires_at", async () => {
    const res = await mint(authed(["operator"]), stagedBody);
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      id: string;
      upload_id: string;
      uploads: { filename: string; url: string; staged_href: string }[];
      expires_at: string;
    };
    expect(body.upload_id).toBe(UPLOAD_ID);
    expect(body.id).toBe(UPLOAD_ID); // audit created-id extraction
    expect(body.expires_at).toBe("2026-08-31T10:00:00.000Z");
    expect(body.uploads).toHaveLength(1);
    const up = body.uploads[0];
    expect(up.filename).toBe("B04.tif");
    expect(up.staged_href).toBe(`staging://${UPLOAD_ID}/B04.tif`);
    // Presigned PUT into the staging prefix, not canonical.
    expect(up.url).toContain(`staging/${UPLOAD_ID}/B04.tif`);
    expect(up.url).toContain("X-Amz-Signature=");

    // The ledger insert carried the binding (group from the settings row,
    // sanitized filenames).
    expect(mockCreate).toHaveBeenCalledWith({
      collectionId: "sentinel-pushed",
      createdBy: "user-1",
      groupId: EO,
      filenames: ["B04.tif"],
    });
  });

  it("leaves canonical mode (body with `item`) DB-free", async () => {
    const res = await mint(authed(["operator"]), {
      ...stagedBody,
      item: "S2A_001",
    });
    expect(res.status).toBe(200);
    expect(mockSettings).not.toHaveBeenCalled();
    expect(mockCreate).not.toHaveBeenCalled();
    const body = (await res.json()) as { uploads: { key: string }[] };
    expect(body.uploads[0].key).toBe("assets/sentinel-pushed/S2A_001/B04.tif");
  });
});

describe("GET /api/uploads/[uploadId] (poll)", () => {
  it("401s an anonymous caller", async () => {
    const res = await poll(anonymous, UPLOAD_ID);
    expect(res.status).toBe(401);
  });

  it("404s a non-uuid id without touching the DB", async () => {
    const res = await poll(authed(["member"]), "not-a-uuid");
    expect(res.status).toBe(404);
    expect(mockGet).not.toHaveBeenCalled();
  });

  it("404s an unknown session", async () => {
    mockGet.mockResolvedValue(null);
    const res = await poll(authed(["member"]), UPLOAD_ID);
    expect(res.status).toBe(404);
  });

  it("404s a session outside the caller's groups (existence is scoped)", async () => {
    mockGet.mockResolvedValue(session({ createdBy: "someone-else" }));
    const res = await poll(authed(["member"], ["other-group"]), UPLOAD_ID);
    expect(res.status).toBe(404);
  });

  it("serves the row to a member of the session's group", async () => {
    mockGet.mockResolvedValue(session({ createdBy: "someone-else" }));
    const res = await poll(authed(["member"]), UPLOAD_ID);
    expect(res.status).toBe(200);
  });

  it("serves an unowned-collection session to its creator", async () => {
    mockGet.mockResolvedValue(session({ groupId: null }));
    const res = await poll(authed(["member"], []), UPLOAD_ID);
    expect(res.status).toBe(200);
  });

  it("serves any session to an admin", async () => {
    mockGet.mockResolvedValue(
      session({ groupId: "other-group", createdBy: "someone-else" }),
    );
    const res = await poll(authed(["admin"], []), UPLOAD_ID);
    expect(res.status).toBe(200);
  });

  it("returns the ADR 0004 poll shape with the pipeline-written verdict", async () => {
    mockGet.mockResolvedValue(
      session({
        itemId: "S2A_001",
        status: "rejected",
        result: { rejected: [{ reason: "missing_bytes" }] },
        error: "staged object B04.tif was never uploaded",
        finalizedAt: "2026-08-30T11:00:00.000Z",
      }),
    );
    const res = await poll(authed(["member"]), UPLOAD_ID);
    expect(res.status).toBe(200);
    const { upload } = (await res.json()) as { upload: Record<string, unknown> };
    expect(upload).toEqual({
      id: UPLOAD_ID,
      collection_id: "sentinel-pushed",
      item_id: "S2A_001",
      filenames: ["B04.tif"],
      status: "rejected",
      result: { rejected: [{ reason: "missing_bytes" }] },
      error: "staged object B04.tif was never uploaded",
      created_at: "2026-08-30T10:00:00.000Z",
      expires_at: "2026-08-31T10:00:00.000Z",
      finalized_at: "2026-08-30T11:00:00.000Z",
    });
  });

  it("does not leak internal columns (created_by, group_id, prior_item)", async () => {
    mockGet.mockResolvedValue(session());
    const res = await poll(authed(["member"]), UPLOAD_ID);
    const { upload } = (await res.json()) as { upload: Record<string, unknown> };
    expect(upload).not.toHaveProperty("created_by");
    expect(upload).not.toHaveProperty("group_id");
    expect(upload).not.toHaveProperty("prior_item");
  });

  it("500s a row that violates the push-upload-status contract", async () => {
    mockGet.mockResolvedValue(session({ status: "uploading" as never }));
    const res = await poll(authed(["member"]), UPLOAD_ID);
    expect(res.status).toBe(500);
  });
});
