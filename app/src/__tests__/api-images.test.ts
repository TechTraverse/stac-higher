// @vitest-environment node
/**
 * /api/images read routes and the add verb (C-3, container-images spec
 * §9.1): auth, normalization, the allowed_registries check, the credential's
 * group and host, dedup of an open admission, the fail-closed policy, the
 * group-scoped "in use by" list, and the scan poll across the drain's dedup.
 * Storage is mocked: this pins ROUTE behaviour, not SQL.
 *
 * Controller rulings amending the plan's brief (each pinned here):
 *   - F13 (final form): a registry connection with `enabled: false` is
 *     refused with 422 `registry_connection_disabled`, but only AFTER the
 *     group-access and protocol checks — a foreign-group or wrong-protocol
 *     credential answers exactly as it would if it were enabled, never
 *     revealing that it happens to be disabled.
 *   - Every route taking an image or scan id in the path validates it as a
 *     UUID first and answers 400 (not a 404, and never a 500 from a Postgres
 *     cast error) for a malformed id.
 *   - A blank or whitespace-only `tag` is "no tag" (consistent with
 *     `normalizeImageInput`), not a 400.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({
  listImages: vi.fn(),
  getImage: vi.fn(),
  listImageScans: vi.fn(),
  listImageUsers: vi.fn(),
  getImageScan: vi.fn(),
  findOpenAdmission: vi.fn(),
  insertImageWithAdmission: vi.fn(),
  requestImageScan: vi.fn(),
  grantImageException: vi.fn(),
  revokeImage: vi.fn(),
  isDigestLaunchable: vi.fn(),
  getImageForGate: vi.fn(),
}));
vi.mock("@/lib/connections/storage", () => ({ getConnection: vi.fn() }));
vi.mock("@/lib/storage/presign", () => ({
  presignGetUrl: vi.fn(async (key: string) => `https://objects.example/${key}?sig=1`),
}));
vi.mock("@/lib/db/migrate", () => ({ runMigrations: vi.fn(async () => {}) }));

import type { AuthContext, CanonicalRole } from "@/lib/auth/types";
import { getConnection } from "@/lib/connections/storage";
import { resetImagePolicyCache } from "@/lib/images/policy";
import {
  findOpenAdmission,
  getImage,
  getImageScan,
  insertImageWithAdmission,
  listImageScans,
  listImageUsers,
  listImages,
  type ApiImage,
  type ApiImageScan,
} from "@/lib/images/storage";
import { GET as listRoute, POST as addRoute } from "@/pages/api/images/index";
import { GET as detailRoute } from "@/pages/api/images/[id]";
import { GET as scanRoute } from "@/pages/api/images/[id]/scans/[scanId]";
import { GET as findingsRoute } from "@/pages/api/images/[id]/scans/[scanId]/findings";

const EO = "earth-observation";
const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const OTHER_IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e71";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";
const CONN = "3a9f1c2e-0000-4000-8000-000000000001";

function authed(roles: CanonicalRole[], groups = [EO]): AuthContext {
  return {
    authenticated: true,
    mode: "bypass",
    identity: { sub: "user-1", email: null, name: null, groups, roles },
  };
}
const anon: AuthContext = { authenticated: false, mode: "oidc", identity: null };
const member = authed(["member"]);
const operator = authed(["operator"]);
const admin = authed(["admin"], []);

type RouteHandler = (ctx: never) => Promise<Response> | Response;

async function call(
  handler: RouteHandler,
  auth: AuthContext,
  {
    method = "GET",
    body,
    params = {},
    search = "",
  }: { method?: string; body?: unknown; params?: Record<string, string>; search?: string } = {},
) {
  const url = new URL(`http://localhost:4321/api/images${search}`);
  const locals: { auth: AuthContext; auditDetail?: Record<string, unknown> } = { auth };
  const res = await handler({
    url,
    locals,
    params,
    request: new Request(url, {
      method,
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  } as never);
  return { res, locals };
}

function image(overrides: Partial<ApiImage> = {}): ApiImage {
  return {
    id: IMG,
    reference: "docker.io/library/python",
    tag_at_add: "3.12-slim",
    digest: null,
    status: "pending",
    verdict: null,
    size_bytes: null,
    config: null,
    last_scan_id: null,
    last_scanned_at: null,
    stale: false,
    db_built_at: null,
    added_by: "user-1",
    created_at: "2026-09-27T00:00:00.000Z",
    updated_at: "2026-09-27T00:00:00.000Z",
    exception: null,
    tag_current_digest: null,
    tag_checked_at: null,
    drifted: false,
    registry_connection: null,
    in_use_by: 0,
    ...overrides,
  };
}

function scan(overrides: Partial<ApiImageScan> = {}): ApiImageScan {
  return {
    id: SCAN,
    image_id: IMG,
    kind: "admission",
    status: "pending",
    requested_by: "user-1",
    requested_at: "2026-09-27T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    result: null,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

function registryConnection(overrides: Record<string, unknown> = {}) {
  return {
    id: CONN,
    name: "ghcr robot",
    description: "",
    protocol: "registry",
    config: { host: "ghcr.io" },
    credentials_set: true,
    host_key: null,
    group_id: EO,
    created_by: "user-1",
    created_at: "2026-09-01T00:00:00.000Z",
    updated_at: "2026-09-01T00:00:00.000Z",
    enabled: true,
    status: "ok",
    last_checked_at: null,
    last_error: null,
    ...overrides,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  resetImagePolicyCache();
  vi.mocked(findOpenAdmission).mockResolvedValue(null);
  vi.mocked(insertImageWithAdmission).mockResolvedValue({
    image_id: IMG,
    scan_id: SCAN,
    deduplicated: false,
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
  resetImagePolicyCache();
});

function breakPolicy() {
  vi.stubEnv("PROCESS_IMAGE_POLICY_FILE", "/nonexistent/image-policy.json");
  resetImagePolicyCache();
}

describe("GET /api/images", () => {
  it("is member+ and 401 for anonymous", async () => {
    expect((await call(listRoute, anon)).res.status).toBe(401);
    vi.mocked(listImages).mockResolvedValue([image()]);
    const { res } = await call(listRoute, member);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ images: [image()], scan_window_days: 30 });
  });

  it("passes the filters through", async () => {
    vi.mocked(listImages).mockResolvedValue([]);
    await call(listRoute, member, { search: "?status=flagged&q=%20satpy%20&in_use=true" });
    expect(vi.mocked(listImages).mock.calls[0][0]).toEqual({
      status: "flagged",
      q: "satpy",
      inUse: true,
    });
    await call(listRoute, member, { search: "?in_use=false" });
    expect(vi.mocked(listImages).mock.calls[1][0]).toEqual({
      status: undefined,
      q: undefined,
      inUse: false,
    });
  });

  it("refuses an unknown status or in_use value", async () => {
    expect((await call(listRoute, member, { search: "?status=stale" })).res.status).toBe(400);
    expect((await call(listRoute, member, { search: "?in_use=yes" })).res.status).toBe(400);
    expect(listImages).not.toHaveBeenCalled();
  });

  it("still lists when the policy is unreadable, with the window unknown", async () => {
    breakPolicy();
    vi.mocked(listImages).mockResolvedValue([]);
    const { res } = await call(listRoute, member);
    expect(res.status).toBe(200);
    expect((await res.json()).scan_window_days).toBeNull();
    expect(vi.mocked(listImages).mock.calls[0][1].scanWindowDays).toBeNull();
  });
});

describe("POST /api/images", () => {
  it("is operator+", async () => {
    expect((await call(addRoute, anon, { method: "POST", body: { reference: "python" } })).res.status).toBe(401);
    const { res } = await call(addRoute, member, { method: "POST", body: { reference: "python" } });
    expect(res.status).toBe(403);
    expect((await res.json()).code).toBe("forbidden");
  });

  it("normalizes the reference, writes a pending row + admission scan, and answers 202", async () => {
    const { res, locals } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python:3.12-slim" },
    });
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({
      id: IMG,
      image_id: IMG,
      scan_id: SCAN,
      deduplicated: false,
      reference: "docker.io/library/python",
      tag: "3.12-slim",
    });
    expect(insertImageWithAdmission).toHaveBeenCalledWith({
      reference: "docker.io/library/python",
      tag: "3.12-slim",
      registryConnectionId: null,
      addedBy: "user-1",
    });
    expect(locals.auditDetail).toEqual({
      reference: "docker.io/library/python",
      tag: "3.12-slim",
      registry_connection_id: null,
      scan_id: SCAN,
      deduplicated: false,
    });
  });

  it("treats a blank or whitespace-only tag as no tag (the default)", async () => {
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python", tag: "" },
    });
    expect(res.status).toBe(202);
    expect(await res.json()).toMatchObject({ tag: "latest" });
    expect(insertImageWithAdmission).toHaveBeenCalledWith({
      reference: "docker.io/library/python",
      tag: "latest",
      registryConnectionId: null,
      addedBy: "user-1",
    });

    vi.mocked(insertImageWithAdmission).mockClear();
    const { res: res2 } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python", tag: "   " },
    });
    expect(res2.status).toBe(202);
    expect(insertImageWithAdmission).toHaveBeenCalledWith({
      reference: "docker.io/library/python",
      tag: "latest",
      registryConnectionId: null,
      addedBy: "user-1",
    });
  });

  it("refuses a body it cannot read and a reference it cannot store", async () => {
    expect((await call(addRoute, operator, { method: "POST", body: { ref: "python" } })).res.status).toBe(400);
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python@sha256:" + "a".repeat(64) },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("invalid_reference");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("refuses a registry outside the policy before writing anything", async () => {
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "quay.io/org/tool:1" },
    });
    expect(res.status).toBe(422);
    const body = await res.json();
    expect(body.code).toBe("registry_not_allowed");
    expect(body.error).toMatch(/quay\.io/);
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("allows a wildcard-matched ECR host", async () => {
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/app:prod" },
    });
    expect(res.status).toBe(202);
  });

  it("fails closed when the policy is unreadable", async () => {
    breakPolicy();
    const { res } = await call(addRoute, operator, { method: "POST", body: { reference: "python" } });
    expect(res.status).toBe(503);
    expect((await res.json()).code).toBe("image_policy_unavailable");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("accepts a registry credential of the caller's group for the same host", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection());
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/satpy-runtime:1.4.2", registry_connection_id: CONN },
    });
    expect(res.status).toBe(202);
    expect(vi.mocked(insertImageWithAdmission).mock.calls[0][0].registryConnectionId).toBe(CONN);
  });

  it("matches Docker Hub credentials through the host aliases", async () => {
    vi.mocked(getConnection).mockResolvedValue(
      registryConnection({ config: { host: "index.docker.io" } }),
    );
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "org/private-tool:2", registry_connection_id: CONN },
    });
    expect(res.status).toBe(202);
  });

  it("hides a credential outside the caller's groups (404) and refuses a non-registry one", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ group_id: "weather" }));
    let { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    });
    expect(res.status).toBe(404);
    expect((await res.json()).code).toBe("registry_connection_not_found");

    vi.mocked(getConnection).mockResolvedValue(null);
    ({ res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    }));
    expect(res.status).toBe(404);

    vi.mocked(getConnection).mockResolvedValue(registryConnection({ protocol: "s3", config: { bucket: "b" } }));
    ({ res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    }));
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("not_a_registry_connection");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("an admin may use any group's credential", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ group_id: "weather" }));
    const { res } = await call(addRoute, admin, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    });
    expect(res.status).toBe(202);
  });

  it("refuses a credential for another registry host", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ config: { host: "ghcr.io" } }));
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "python", registry_connection_id: CONN },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("registry_connection_host_mismatch");
  });

  it("refuses a disabled registry connection of the caller's own group (F13)", async () => {
    vi.mocked(getConnection).mockResolvedValue(registryConnection({ enabled: false }));
    const { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).code).toBe("registry_connection_disabled");
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });

  it("never reveals a disabled state for a credential the group/protocol checks already refuse", async () => {
    // Foreign group: still 404, not 422 registry_connection_disabled.
    vi.mocked(getConnection).mockResolvedValue(
      registryConnection({ group_id: "weather", enabled: false }),
    );
    let { res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    });
    expect(res.status).toBe(404);
    expect((await res.json()).code).toBe("registry_connection_not_found");

    // Wrong protocol: still 400 not_a_registry_connection, not the disabled code.
    vi.mocked(getConnection).mockResolvedValue(
      registryConnection({ protocol: "s3", config: { bucket: "b" }, enabled: false }),
    );
    ({ res } = await call(addRoute, operator, {
      method: "POST",
      body: { reference: "ghcr.io/org/img", registry_connection_id: CONN },
    }));
    expect(res.status).toBe(400);
    expect((await res.json()).code).toBe("not_a_registry_connection");
  });

  it("reuses an open admission instead of queueing a second provisional row", async () => {
    vi.mocked(findOpenAdmission).mockResolvedValue({
      image_id: IMG,
      scan_id: SCAN,
      deduplicated: true,
    });
    const { res } = await call(addRoute, operator, { method: "POST", body: { reference: "python" } });
    expect(res.status).toBe(202);
    expect((await res.json()).deduplicated).toBe(true);
    expect(findOpenAdmission).toHaveBeenCalledWith({
      reference: "docker.io/library/python",
      tag: "latest",
      registryConnectionId: null,
    });
    expect(insertImageWithAdmission).not.toHaveBeenCalled();
  });
});

describe("GET /api/images/[id]", () => {
  it("400s a malformed id and 404s a well-formed but missing one", async () => {
    expect((await call(detailRoute, member, { params: { id: "nope" } })).res.status).toBe(400);
    vi.mocked(getImage).mockResolvedValue(null);
    expect((await call(detailRoute, member, { params: { id: IMG } })).res.status).toBe(404);
  });

  it("names only the processes in the caller's groups and counts the rest", async () => {
    vi.mocked(getImage).mockResolvedValue(image({ status: "approved", in_use_by: 2 }));
    vi.mocked(listImageScans).mockResolvedValue([scan({ status: "done" })]);
    vi.mocked(listImageUsers).mockResolvedValue([
      { process_id: "p-1", name: "geocolor", group_id: EO },
      { process_id: "p-2", name: "radar", group_id: "weather" },
    ]);
    const { res } = await call(detailRoute, member, { params: { id: IMG } });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.in_use_by).toEqual([{ process_id: "p-1", name: "geocolor", group_id: EO }]);
    expect(body.in_use_elsewhere).toBe(1);
    expect(body.scans).toHaveLength(1);
    expect(vi.mocked(listImageScans).mock.calls[0]).toEqual([IMG, 10]);

    const asAdmin = await call(detailRoute, admin, { params: { id: IMG } });
    const adminBody = await asAdmin.res.json();
    expect(adminBody.in_use_by).toHaveLength(2);
    expect(adminBody.in_use_elsewhere).toBe(0);
  });

  it("passes a lower-cased id to storage", async () => {
    vi.mocked(getImage).mockResolvedValue(image());
    vi.mocked(listImageScans).mockResolvedValue([]);
    vi.mocked(listImageUsers).mockResolvedValue([]);
    await call(detailRoute, member, { params: { id: IMG.toUpperCase() } });
    expect(vi.mocked(getImage).mock.calls[0][0]).toBe(IMG);
  });
});

describe("GET /api/images/[id]/scans/[scanId]", () => {
  it("returns the scan with the AUTHORITATIVE image (the drain may have re-pointed it)", async () => {
    vi.mocked(getImageScan).mockResolvedValue(scan({ image_id: OTHER_IMG, status: "done" }));
    vi.mocked(getImage).mockResolvedValue(image({ id: OTHER_IMG, status: "approved" }));
    const { res } = await call(scanRoute, member, { params: { id: IMG, scanId: SCAN } });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.scan.image_id).toBe(OTHER_IMG);
    expect(body.image.id).toBe(OTHER_IMG);
    expect(vi.mocked(getImageScan)).toHaveBeenCalledWith(IMG, SCAN);
    expect(vi.mocked(getImage).mock.calls[0][0]).toBe(OTHER_IMG);
  });

  it("404s an unknown scan, 400s a malformed scan id, and 401s anonymous", async () => {
    vi.mocked(getImageScan).mockResolvedValue(null);
    expect((await call(scanRoute, member, { params: { id: IMG, scanId: SCAN } })).res.status).toBe(404);
    expect((await call(scanRoute, member, { params: { id: IMG, scanId: "x" } })).res.status).toBe(400);
    expect((await call(scanRoute, member, { params: { id: "x", scanId: SCAN } })).res.status).toBe(400);
    expect((await call(scanRoute, anon, { params: { id: IMG, scanId: SCAN } })).res.status).toBe(401);
  });
});

describe("GET /api/images/[id]/scans/[scanId]/findings", () => {
  it("redirects to a short-lived presigned URL, never cached", async () => {
    vi.mocked(getImageScan).mockResolvedValue(
      scan({ status: "done", findings_ref: "scans/i/s/findings.grype.json" }),
    );
    const { res } = await call(findingsRoute, member, { params: { id: IMG, scanId: SCAN } });
    expect(res.status).toBe(302);
    expect(res.headers.get("Location")).toBe(
      "https://objects.example/scans/i/s/findings.grype.json?sig=1",
    );
    expect(res.headers.get("Cache-Control")).toBe("private, no-store");
  });

  it("404s a scan with no stored findings", async () => {
    vi.mocked(getImageScan).mockResolvedValue(scan());
    const { res } = await call(findingsRoute, member, { params: { id: IMG, scanId: SCAN } });
    expect(res.status).toBe(404);
  });

  it("400s a malformed image or scan id", async () => {
    expect((await call(findingsRoute, member, { params: { id: "nope", scanId: SCAN } })).res.status).toBe(
      400,
    );
    expect((await call(findingsRoute, member, { params: { id: IMG, scanId: "nope" } })).res.status).toBe(
      400,
    );
    expect(getImageScan).not.toHaveBeenCalled();
  });
});
