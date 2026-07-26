// @vitest-environment node
// (pure policy table — no DOM involved)
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const COLLECTION = "sentinel-2";
const ASSOC = "3a9f1c2e-0000-4000-8000-0000000000a1";

describe("matchGatedRoute — /api/collections/[id]/connections (Phase 4)", () => {
  const base = `/api/collections/${COLLECTION}/connections`;

  it("gates POST as a collection_connection create (id filled from the 201 body)", () => {
    expect(matchGatedRoute("POST", base)).toEqual({
      action: "create",
      resourceType: "collection_connection",
      resourceId: null,
    });
  });

  it("gates PUT/PATCH/DELETE on a single association with its path id", () => {
    expect(matchGatedRoute("PUT", `${base}/${ASSOC}`)).toEqual({
      action: "update",
      resourceType: "collection_connection",
      resourceId: ASSOC,
    });
    expect(matchGatedRoute("PATCH", `${base}/${ASSOC}`)?.action).toBe("update");
    expect(matchGatedRoute("DELETE", `${base}/${ASSOC}`)).toEqual({
      action: "delete",
      resourceType: "collection_connection",
      resourceId: ASSOC,
    });
  });

  it("leaves GETs (list + detail) open", () => {
    expect(matchGatedRoute("GET", base)).toBeNull();
    expect(matchGatedRoute("GET", `${base}/${ASSOC}`)).toBeNull();
  });

  it("gates POST .../redeliver as an audited redeliver action (Slice D)", () => {
    const DELIVERY = "3a9f1c2e-0000-4000-8000-0000000000d1";
    expect(
      matchGatedRoute(
        "POST",
        `${base}/${ASSOC}/deliveries/${DELIVERY}/redeliver`,
      ),
    ).toEqual({
      action: "redeliver",
      resourceType: "collection_connection",
      resourceId: ASSOC,
    });
    // Reading delivery status stays an open read.
    expect(matchGatedRoute("GET", `${base}/${ASSOC}/deliveries`)).toBeNull();
  });

  it("gates POST .../backfill as an audited backfill action (Slice C)", () => {
    expect(matchGatedRoute("POST", `${base}/${ASSOC}/backfill`)).toEqual({
      action: "backfill",
      resourceType: "collection_connection",
      resourceId: ASSOC,
    });
    // Polling a backfill stays an open read.
    expect(
      matchGatedRoute("GET", `${base}/${ASSOC}/backfills/some-id`),
    ).toBeNull();
  });
});
