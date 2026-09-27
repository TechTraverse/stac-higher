import { describe, expect, it } from "vitest";
import {
  DEFAULT_IMAGE_TAG,
  canonicalRegistryHost,
  normalizeImageInput,
} from "@/lib/images/normalize";
import { isImageStale } from "@/lib/images/stale";
import { exceptionLapsed, readVerdict } from "@/lib/images/verdict";
import { imageAddSchema, imageExceptionSchema } from "@/lib/images/schemas";
import { isImageReference } from "@/lib/images/reference";

function ok(typed: string, tag?: string | null) {
  const result = normalizeImageInput(typed, tag);
  if (!result.ok) throw new Error(`expected ok for ${typed}: ${result.error}`);
  return { reference: result.reference, tag: result.tag };
}

function refused(typed: string, tag?: string | null): string {
  const result = normalizeImageInput(typed, tag);
  if (result.ok) throw new Error(`expected a refusal for ${typed}`);
  return result.error;
}

describe("normalizeImageInput (C-3: what a person types -> C-1's stored grammar)", () => {
  it.each([
    ["python", { reference: "docker.io/library/python", tag: DEFAULT_IMAGE_TAG }],
    ["python:3.12-slim", { reference: "docker.io/library/python", tag: "3.12-slim" }],
    ["docker.io/python", { reference: "docker.io/library/python", tag: "latest" }],
    ["index.docker.io/library/python:3", { reference: "docker.io/library/python", tag: "3" }],
    ["registry-1.docker.io/org/tool:1", { reference: "docker.io/org/tool", tag: "1" }],
    ["Org/Tool:V1", { reference: "docker.io/org/tool", tag: "V1" }],
    ["GHCR.IO/Org/Img", { reference: "ghcr.io/org/img", tag: "latest" }],
    ["ghcr.io/org/satpy-runtime:1.4.2", { reference: "ghcr.io/org/satpy-runtime", tag: "1.4.2" }],
    ["localhost:5000/team/img", { reference: "localhost:5000/team/img", tag: "latest" }],
    ["localhost:5000/team/img:dev", { reference: "localhost:5000/team/img", tag: "dev" }],
    [
      "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/app:prod",
      { reference: "123456789012.dkr.ecr.us-gov-west-1.amazonaws.com/team/app", tag: "prod" },
    ],
    ["  ghcr.io/org/img  ", { reference: "ghcr.io/org/img", tag: "latest" }],
  ])("normalizes %s", (typed, expected) => {
    const result = ok(typed);
    expect(result).toEqual(expected);
    expect(isImageReference(result.reference)).toBe(true);
  });

  it("takes the tag field when the reference carries none, and agrees when both match", () => {
    expect(ok("ghcr.io/org/img", "2.0")).toEqual({ reference: "ghcr.io/org/img", tag: "2.0" });
    expect(ok("ghcr.io/org/img:2.0", "2.0")).toEqual({ reference: "ghcr.io/org/img", tag: "2.0" });
    expect(ok("ghcr.io/org/img", "  ")).toEqual({ reference: "ghcr.io/org/img", tag: "latest" });
  });

  it("refuses what it cannot store, with a reason a person can act on", () => {
    expect(refused("")).toMatch(/Enter an image reference/);
    expect(refused("https://ghcr.io/org/img")).toMatch(/scheme/);
    expect(refused("python@sha256:" + "a".repeat(64))).toMatch(/by tag, not by digest/);
    expect(refused("ghcr.io/org/img:1", "2")).toMatch(/"1".*"2"/);
    expect(refused("ghcr.io/org/img:")).toMatch(/not a valid tag/);
    expect(refused("ghcr.io/org/my img")).toMatch(/no spaces/);
    expect(refused("ghcr.io/org/-bad")).toMatch(/not an image reference/);
  });

  it("folds the Docker Hub host aliases to docker.io", () => {
    expect(canonicalRegistryHost("Index.Docker.IO")).toBe("docker.io");
    expect(canonicalRegistryHost("registry-1.docker.io")).toBe("docker.io");
    expect(canonicalRegistryHost("GHCR.io")).toBe("ghcr.io");
  });
});

describe("isImageStale (spec §4.3: computed, never stored)", () => {
  const now = new Date("2026-09-27T12:00:00.000Z");
  const DAY = 86_400_000;

  it("uses the gate's boundary: exactly the window ago is fresh, a millisecond older is stale", () => {
    const edge = new Date(now.getTime() - 30 * DAY);
    expect(isImageStale("approved", edge, 30, now)).toBe(false);
    expect(isImageStale("approved", new Date(edge.getTime() - 1), 30, now)).toBe(true);
    expect(isImageStale("flagged", edge.toISOString(), 30, now)).toBe(false);
  });

  it("calls a scanned-status image with no scan time stale", () => {
    expect(isImageStale("approved", null, 30, now)).toBe(true);
  });

  it("is never stale for statuses that have not passed a scan", () => {
    for (const status of ["pending", "scanning", "rejected", "revoked", "scan_failed"] as const) {
      expect(isImageStale(status, null, 30, now)).toBe(false);
    }
  });

  it("is unknown (null) when the policy window is unknown", () => {
    expect(isImageStale("approved", now, null, now)).toBeNull();
  });

  it("fails closed on an unparseable scan time (never reads NaN as fresh)", () => {
    expect(isImageStale("approved", "not-a-date", 30, now)).toBe(true);
  });
});

describe("readVerdict (lenient: the pipeline owns the shape)", () => {
  it("reads the spec §4.1 verdict and keeps defaults for missing lists", () => {
    expect(
      readVerdict({ pass: false, reasons: ["kev:CVE-2026-1"], counts: { critical: 1 }, extra: 1 }),
    ).toEqual({ pass: false, reasons: ["kev:CVE-2026-1"], counts: { critical: 1 }, kev: [] });
  });

  it("answers null for anything that is not a verdict", () => {
    expect(readVerdict(null)).toBeNull();
    expect(readVerdict({ reasons: [] })).toBeNull();
    expect(readVerdict("pass")).toBeNull();
  });
});

describe("exceptionLapsed (controller ruling, C-3: an expired exception needs a passing verdict)", () => {
  const now = new Date("2026-09-27T12:00:00.000Z");
  const DAY = 86_400_000;
  const past = new Date(now.getTime() - DAY);
  const future = new Date(now.getTime() + DAY);

  it("is false for anything that is not an approved image with an exception", () => {
    expect(exceptionLapsed({ status: "flagged", exceptionExpiresAt: past, verdict: null }, now)).toBe(false);
    expect(exceptionLapsed({ status: "approved", exceptionExpiresAt: null, verdict: null }, now)).toBe(false);
  });

  it("is false for a live exception, whatever the verdict says", () => {
    expect(
      exceptionLapsed({ status: "approved", exceptionExpiresAt: future, verdict: { pass: false } }, now),
    ).toBe(false);
  });

  it("expiry exactly now counts as expired (same boundary as the gate)", () => {
    expect(exceptionLapsed({ status: "approved", exceptionExpiresAt: now, verdict: null }, now)).toBe(true);
  });

  it("an expired exception only lapses when the latest verdict does not pass", () => {
    expect(
      exceptionLapsed({ status: "approved", exceptionExpiresAt: past, verdict: { pass: true } }, now),
    ).toBe(false);
    expect(
      exceptionLapsed({ status: "approved", exceptionExpiresAt: past, verdict: { pass: false } }, now),
    ).toBe(true);
    expect(exceptionLapsed({ status: "approved", exceptionExpiresAt: past, verdict: null }, now)).toBe(true);
  });
});

describe("request schemas", () => {
  it("accepts an add body and refuses unknown keys", () => {
    expect(imageAddSchema.safeParse({ reference: "python" }).success).toBe(true);
    expect(
      imageAddSchema.safeParse({
        reference: "ghcr.io/org/img",
        tag: "1.0",
        registry_connection_id: "3a9f1c2e-0000-4000-8000-000000000001",
      }).success,
    ).toBe(true);
    expect(imageAddSchema.safeParse({ reference: "python", digest: "x" }).success).toBe(false);
    expect(imageAddSchema.safeParse({ reference: "   " }).success).toBe(false);
  });

  it("requires a real reason and an offset timestamp for an exception", () => {
    expect(
      imageExceptionSchema.safeParse({
        reason: "Vendor fix lands next sprint; tracked in TT-12",
        expires_at: "2026-10-27T00:00:00Z",
      }).success,
    ).toBe(true);
    expect(
      imageExceptionSchema.safeParse({ reason: "ok", expires_at: "2026-10-27T00:00:00Z" }).success,
    ).toBe(false);
    expect(
      imageExceptionSchema.safeParse({ reason: "A long enough reason", expires_at: "next week" }).success,
    ).toBe(false);
  });
});
