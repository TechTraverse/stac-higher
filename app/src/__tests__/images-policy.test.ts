// @vitest-environment node
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  ImagePolicyUnavailable,
  imagePolicyPath,
  loadImagePolicy,
  resetImagePolicyCache,
} from "@/lib/images/policy";

afterEach(() => resetImagePolicyCache());

describe("loadImagePolicy", () => {
  it("reads the checkout default when PROCESS_IMAGE_POLICY_FILE is unset", () => {
    expect(imagePolicyPath({})).toMatch(/infra\/image-policy\/default\.json$/);
    expect(loadImagePolicy({}).scan_window_days).toBe(30);
  });

  it("fails closed on a missing file, naming the path", () => {
    expect(() => loadImagePolicy({ PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" })).toThrow(
      ImagePolicyUnavailable,
    );
    expect(() => loadImagePolicy({ PROCESS_IMAGE_POLICY_FILE: "/nonexistent/policy.json" })).toThrow(
      /\/nonexistent\/policy\.json/,
    );
  });
});

describe("the app image ships the policy (the K-1 packaging pattern)", () => {
  const dockerfile = readFileSync(
    fileURLToPath(new URL("../../Dockerfile", import.meta.url)),
    "utf8",
  );
  it("copies it through the imagepolicy build context and publishes the path", () => {
    expect(dockerfile).toContain("COPY --from=imagepolicy default.json /app/share/image-policy/default.json");
    expect(dockerfile).toContain("PROCESS_IMAGE_POLICY_FILE=/app/share/image-policy/default.json");
  });
});
