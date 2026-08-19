// @vitest-environment node
/** M2-E: the collection-settings PUT is gated + audited. */
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

describe("matchGatedRoute — /api/collections/[id]/settings (M2-E)", () => {
  it("gates PUT as a collection_settings update", () => {
    expect(matchGatedRoute("PUT", "/api/collections/sentinel-2/settings")).toEqual({
      action: "update",
      resourceType: "collection_settings",
      resourceId: "sentinel-2",
    });
  });

  it("leaves the read ungated (auth enforced in-route)", () => {
    expect(matchGatedRoute("GET", "/api/collections/sentinel-2/settings")).toBeNull();
  });

  it("does not shadow the association routes", () => {
    expect(
      matchGatedRoute("PUT", "/api/collections/sentinel-2/connections/abc"),
    ).toEqual({
      action: "update",
      resourceType: "collection_connection",
      resourceId: "abc",
    });
  });
});
