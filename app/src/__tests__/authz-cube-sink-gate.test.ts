// @vitest-environment node
/** Z-2: the cube sink mutations are gated + audited as `cube_sink`. */
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const PATH = "/api/collections/goes19-c13-cube/cube-sink";

describe("matchGatedRoute — /api/collections/[id]/cube-sink (Z-2)", () => {
  it.each([
    ["PUT", "update"],
    ["PATCH", "update"],
    ["DELETE", "delete"],
  ])("gates %s as a cube_sink %s on the cube collection", (method, action) => {
    expect(matchGatedRoute(method, PATH)).toEqual({
      action,
      resourceType: "cube_sink",
      resourceId: "goes19-c13-cube",
    });
  });

  it("leaves the read ungated (auth enforced in-route)", () => {
    expect(matchGatedRoute("GET", PATH)).toBeNull();
  });

  it("does not shadow collection settings", () => {
    expect(matchGatedRoute("PUT", "/api/collections/goes19-c13-cube/settings")?.resourceType).toBe(
      "collection_settings",
    );
  });
});
