// @vitest-environment node
/** M2-C: notification-channel mutations are gated + audited (spec §4, §5). */
import { describe, it, expect } from "vitest";
import { matchGatedRoute } from "@/lib/authz/permissions";

const ID = "3a9f1c2e-0000-4000-8000-0000000000c1";

describe("matchGatedRoute — /api/channels (M2-C)", () => {
  it("gates create", () => {
    expect(matchGatedRoute("POST", "/api/channels")).toEqual({
      action: "create",
      resourceType: "notification_channel",
      resourceId: null,
    });
  });

  it("gates update and delete with the row id", () => {
    expect(matchGatedRoute("PUT", `/api/channels/${ID}`)).toEqual({
      action: "update",
      resourceType: "notification_channel",
      resourceId: ID,
    });
    expect(matchGatedRoute("DELETE", `/api/channels/${ID}`)).toEqual({
      action: "delete",
      resourceType: "notification_channel",
      resourceId: ID,
    });
  });

  it("leaves reads ungated (auth enforced in-route)", () => {
    expect(matchGatedRoute("GET", "/api/channels")).toBeNull();
    expect(matchGatedRoute("GET", `/api/channels/${ID}`)).toBeNull();
  });

  it("leaves the personal read watermark ungated (member+ in-route)", () => {
    expect(matchGatedRoute("POST", "/api/alerts/read")).toBeNull();
    expect(matchGatedRoute("GET", "/api/alerts/unread")).toBeNull();
  });
});
