// @vitest-environment node
/**
 * C-3: image mutations are gated (operator+ at the guard; exception and
 * revoke are admin, enforced in-route) and audited as their own actions on
 * `container_image`. Reads, the policy route and the internal probe stay
 * ungated.
 */
import { describe, expect, it } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const IMG = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f";
const SCAN = "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e70";

describe("matchGatedRoute — /api/images (C-3)", () => {
  it("audits adding an image as a create of container_image", () => {
    expect(matchGatedRoute("POST", "/api/images")).toEqual({
      action: "create",
      resourceType: "container_image",
      resourceId: null,
    });
    expect(matchGatedRoute("POST", "/api/images/")).toEqual({
      action: "create",
      resourceType: "container_image",
      resourceId: null,
    });
  });

  it.each(["rescan", "exception", "revoke"] as const)(
    "audits %s as its own action against the image id",
    (verb) => {
      expect(matchGatedRoute("POST", `/api/images/${IMG}/${verb}`)).toEqual({
        action: verb,
        resourceType: "container_image",
        resourceId: IMG,
      });
    },
  );

  it("leaves every read ungated", () => {
    expect(matchGatedRoute("GET", "/api/images")).toBeNull();
    expect(matchGatedRoute("GET", `/api/images/${IMG}`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/images/${IMG}/scans/${SCAN}`)).toBeNull();
    expect(matchGatedRoute("GET", `/api/images/${IMG}/scans/${SCAN}/findings`)).toBeNull();
    expect(matchGatedRoute("GET", "/api/processes/image-policy")).toBeNull();
    expect(matchGatedRoute("GET", "/api/internal/images/approved")).toBeNull();
  });

  it("does not treat an unknown image sub-path as a verb", () => {
    expect(matchGatedRoute("POST", `/api/images/${IMG}/delete`)).toBeNull();
  });
});
