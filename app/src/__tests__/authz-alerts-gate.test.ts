// @vitest-environment node
/** M2-B: the alert lifecycle routes are gated + audited (§3.3, §5). */
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const ID = "3a9f1c2e-0000-4000-8000-0000000000e1";

describe("matchGatedRoute — /api/alerts (M2-B)", () => {
  it("gates ack with the §5 audit action 'ack'", () => {
    expect(matchGatedRoute("POST", `/api/alerts/${ID}/ack`)).toEqual({
      action: "ack",
      resourceType: "alert",
      resourceId: ID,
    });
  });

  it("gates resolve with the audit action 'resolve'", () => {
    expect(matchGatedRoute("POST", `/api/alerts/${ID}/resolve`)).toEqual({
      action: "resolve",
      resourceType: "alert",
      resourceId: ID,
    });
  });

  it("leaves the list read ungated (auth enforced in-route)", () => {
    expect(matchGatedRoute("GET", "/api/alerts")).toBeNull();
    expect(matchGatedRoute("POST", "/api/alerts")).toBeNull();
    expect(matchGatedRoute("POST", `/api/alerts/${ID}/other`)).toBeNull();
  });
});
