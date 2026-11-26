import { describe, it, expect } from "vitest";
import {
  DEFAULT_EXECUTOR_BACKEND,
  PROCESS_EXECUTOR_BACKENDS,
  getExecutorBackend,
} from "@/lib/processes/executor";

/**
 * K-5: `PROCESS_EXECUTOR` names the backend that runs process runs. The
 * pipeline reads the same variable (and fails startup on an unknown value);
 * the hardware-profiles route reports it so the UI can word its wait states.
 */
describe("getExecutorBackend", () => {
  it("defaults to docker when unset or blank", () => {
    expect(getExecutorBackend({})).toBe("docker");
    expect(getExecutorBackend({ PROCESS_EXECUTOR: "  " })).toBe("docker");
    expect(DEFAULT_EXECUTOR_BACKEND).toBe("docker");
  });

  it("is case- and space-tolerant", () => {
    expect(getExecutorBackend({ PROCESS_EXECUTOR: " Kubernetes " })).toBe("kubernetes");
  });

  it("throws on an unknown value, naming the variable and both backends", () => {
    expect(() => getExecutorBackend({ PROCESS_EXECUTOR: "ecs" })).toThrow(
      /PROCESS_EXECUTOR.*docker.*kubernetes/,
    );
  });

  it("knows exactly two backends", () => {
    expect([...PROCESS_EXECUTOR_BACKENDS]).toEqual(["docker", "kubernetes"]);
  });
});
