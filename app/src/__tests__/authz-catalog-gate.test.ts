// @vitest-environment node
// (pure policy table — no DOM involved)
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

describe("matchGatedRoute — /api/catalog (ADR 0008 BFF)", () => {
  it("gates collection create/update/delete", () => {
    expect(matchGatedRoute("POST", "/api/catalog/collections")).toEqual({
      action: "create",
      resourceType: "catalog_collection",
      resourceId: null,
    });
    expect(matchGatedRoute("PUT", "/api/catalog/collections/c1")).toEqual({
      action: "update",
      resourceType: "catalog_collection",
      resourceId: "c1",
    });
    expect(matchGatedRoute("PATCH", "/api/catalog/collections/c1")?.action).toBe(
      "update",
    );
    expect(matchGatedRoute("DELETE", "/api/catalog/collections/c1")).toEqual({
      action: "delete",
      resourceType: "catalog_collection",
      resourceId: "c1",
    });
  });

  it("gates item create/update/delete with a {collection}/{item} id", () => {
    expect(matchGatedRoute("POST", "/api/catalog/collections/c1/items")).toEqual({
      action: "create",
      resourceType: "catalog_item",
      resourceId: null,
    });
    expect(
      matchGatedRoute("PUT", "/api/catalog/collections/c1/items/i1"),
    ).toEqual({
      action: "update",
      resourceType: "catalog_item",
      resourceId: "c1/i1",
    });
    expect(
      matchGatedRoute("DELETE", "/api/catalog/collections/c1/items/i1"),
    ).toEqual({
      action: "delete",
      resourceType: "catalog_item",
      resourceId: "c1/i1",
    });
  });

  it("leaves reads and non-transaction paths open (the route 404s them)", () => {
    expect(matchGatedRoute("GET", "/api/catalog/collections")).toBeNull();
    expect(matchGatedRoute("POST", "/api/catalog/search")).toBeNull();
  });
});
