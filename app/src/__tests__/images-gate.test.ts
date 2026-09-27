// @vitest-environment node
/**
 * The C-1 deploy gate (container-images spec §3): the row named by the
 * snapshot must exist, be approved, match reference+digest, be fresh, and,
 * when it carries a registry credential, that credential must be the
 * process's group's.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/images/storage", () => ({ getImageForGate: vi.fn() }));

import { checkImageGate, evaluateImageGate, imageGateRefused } from "@/lib/images/gate";
import { ImagePolicyUnavailable, resetImagePolicyCache } from "@/lib/images/policy";
import { getImageForGate, type ImageGateRow } from "@/lib/images/storage";

const DIGEST = "sha256:" + "a".repeat(64);
const OTHER_DIGEST = "sha256:" + "b".repeat(64);
const SNAP = { id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f", reference: "ghcr.io/example/satpy-runtime", digest: DIGEST };
const NOW = new Date("2026-09-27T12:00:00.000Z");
const DAY = 86_400_000;
const GROUP = "earth-observation";

function row(overrides: Partial<ImageGateRow> = {}): ImageGateRow {
  return {
    id: SNAP.id,
    reference: SNAP.reference,
    digest: DIGEST,
    status: "approved",
    last_scanned_at: new Date(NOW.getTime() - DAY),
    registry_connection_id: null,
    registry_connection_group_id: null,
    ...overrides,
  };
}

function gate(r: ImageGateRow | null) {
  return evaluateImageGate({ row: r, snapshot: SNAP, processGroupId: GROUP, now: NOW, scanWindowDays: 30 });
}

beforeEach(() => vi.mocked(getImageForGate).mockReset());
afterEach(() => resetImagePolicyCache());

describe("evaluateImageGate", () => {
  it("passes an approved, fresh, public image whose digest matches", () => {
    expect(gate(row())).toBeNull();
  });

  it("refuses an image the registry has never seen", () => {
    expect(gate(null)?.reason).toBe("image_not_approved");
  });

  it("a pending image with no digest yet is not approved (not a digest mismatch)", () => {
    expect(gate(row({ status: "pending", digest: null, last_scanned_at: null }))?.reason).toBe(
      "image_not_approved",
    );
  });

  it.each(["scanning", "rejected", "flagged", "revoked", "scan_failed"] as const)(
    "refuses a %s image (flagged blocks deploys even though it still launches)",
    (status) => {
      const refusal = gate(row({ status }));
      expect(refusal?.reason).toBe("image_not_approved");
      expect(refusal?.message).toContain(status);
    },
  );

  it("refuses a snapshot whose digest is not the row's", () => {
    expect(gate(row({ digest: OTHER_DIGEST }))?.reason).toBe("image_digest_mismatch");
  });

  it("refuses a snapshot whose reference is not the row's", () => {
    expect(gate(row({ reference: "ghcr.io/example/other" }))?.reason).toBe("image_digest_mismatch");
  });

  it("refuses a never-scanned approved row as stale", () => {
    expect(gate(row({ last_scanned_at: null }))?.reason).toBe("image_stale");
  });

  it("is still fresh at exactly scan_window_days, stale one millisecond later", () => {
    expect(gate(row({ last_scanned_at: new Date(NOW.getTime() - 30 * DAY) }))).toBeNull();
    expect(gate(row({ last_scanned_at: new Date(NOW.getTime() - 30 * DAY - 1) }))?.reason).toBe(
      "image_stale",
    );
  });

  it("refuses an image pulled with another group's credential", () => {
    expect(
      gate(row({ registry_connection_id: "c-1", registry_connection_group_id: "weather" }))?.reason,
    ).toBe("image_group_mismatch");
  });

  it("a soft-deleted registry credential is 'another group', never 'public'", () => {
    expect(
      gate(row({ registry_connection_id: "c-1", registry_connection_group_id: null }))?.reason,
    ).toBe("image_group_mismatch");
  });

  it("passes an image pulled with the process's own group's credential", () => {
    expect(
      gate(row({ registry_connection_id: "c-1", registry_connection_group_id: GROUP })),
    ).toBeNull();
  });
});

describe("checkImageGate", () => {
  it("inline revisions never read the policy or the registry", async () => {
    // A deployment whose policy file is broken must still deploy inline code.
    const refusal = await checkImageGate(null, GROUP, {
      env: { PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" },
    });
    expect(refusal).toBeNull();
    expect(getImageForGate).not.toHaveBeenCalled();
  });

  it("fails closed when the policy cannot be read, before touching the DB", async () => {
    await expect(
      checkImageGate(SNAP, GROUP, { env: { PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" } }),
    ).rejects.toBeInstanceOf(ImagePolicyUnavailable);
    expect(getImageForGate).not.toHaveBeenCalled();
  });

  it("uses the policy's scan window (30 days in the default)", async () => {
    vi.mocked(getImageForGate).mockResolvedValue(
      row({ last_scanned_at: new Date(NOW.getTime() - 31 * DAY) }),
    );
    const refusal = await checkImageGate(SNAP, GROUP, { now: NOW, env: {} });
    expect(refusal?.reason).toBe("image_stale");
    expect(refusal?.message).toContain("30-day");
    expect(getImageForGate).toHaveBeenCalledWith(SNAP.id);
  });

  it("refuses everything today: no scanner has approved a row yet", async () => {
    vi.mocked(getImageForGate).mockResolvedValue(null);
    expect((await checkImageGate(SNAP, GROUP, { now: NOW, env: {} }))?.reason).toBe(
      "image_not_approved",
    );
  });
});

describe("imageGateRefused", () => {
  it("is a 422 whose code is the reason", async () => {
    const res = imageGateRefused({ reason: "image_stale", message: "m" });
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual({ error: "m", code: "image_stale" });
  });
});
