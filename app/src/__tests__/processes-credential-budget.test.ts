// @vitest-environment node
import { describe, it, expect } from "vitest";
import {
  credentialBudgetError,
  getCredentialGraceSeconds,
  getMaxCredentialSeconds,
  revisionCredentialBudgetError,
} from "@/lib/processes/credential-budget";

describe("credential budget settings and message", () => {
  it("reads no limit from unset or blank", () => {
    expect(getMaxCredentialSeconds({})).toBeNull();
    expect(getMaxCredentialSeconds({ PROCESS_MAX_CREDENTIAL_SECONDS: " " })).toBeNull();
    expect(getMaxCredentialSeconds({ PROCESS_MAX_CREDENTIAL_SECONDS: "3600" })).toBe(3600);
  });
  it.each(["1h", "0", "-5", "600", "3600.5"])("refuses a malformed cap %j (Review Focus 4)", (raw) => {
    expect(() => getMaxCredentialSeconds({ PROCESS_MAX_CREDENTIAL_SECONDS: raw })).toThrow(
      /PROCESS_MAX_CREDENTIAL_SECONDS/,
    );
  });
  it("reads the grace like the pipeline: default 300, non-negative integer", () => {
    expect(getCredentialGraceSeconds({})).toBe(300);
    expect(getCredentialGraceSeconds({ PROCESS_CREDENTIAL_GRACE_SECONDS: "120" })).toBe(120);
    expect(() => getCredentialGraceSeconds({ PROCESS_CREDENTIAL_GRACE_SECONDS: "five" })).toThrow(
      /PROCESS_CREDENTIAL_GRACE_SECONDS/,
    );
  });
  it("names the budget, its parts and the cap in seconds", () => {
    expect(
      credentialBudgetError({ maxCredentialSeconds: 3600, maxQueueWaitSeconds: 600, timeoutSeconds: 2701, graceSeconds: 300 }),
    ).toBe(
      "credential budget 3601s (max_queue_wait_seconds 600 + timeout_seconds 2701 + grace 300) exceeds this deployment's cap of 3600s (PROCESS_MAX_CREDENTIAL_SECONDS)",
    );
  });

  it("reads no grace when no cap is set (a bad grace never throws)", () => {
    const profiles = { profiles: [] } as never;
    expect(
      revisionCredentialBudgetError(
        { timeout_seconds: 60, hardware: { profile: "x" } },
        profiles,
        { PROCESS_CREDENTIAL_GRACE_SECONDS: "+300" },
      ),
    ).toBeNull();
  });
});
