import { describe, expect, it } from "vitest";
import { formatCommand, parseCommand } from "@/lib/processes/command";
import {
  DEFAULT_RUNTIME_FORM,
  buildRuntimePayload,
  resourcesFromRevision,
  revisionImageDigest,
  runtimeFormFromRevision,
} from "@/components/processes/runtime-form";
import { processRevisionCreateSchema } from "@/lib/processes/schemas";

const SNAP = {
  id: "7c1e2f4a-3b5d-4c6e-8f90-1a2b3c4d5e6f",
  reference: "ghcr.io/org/satpy-runtime",
  digest: "sha256:" + "a".repeat(64),
};
const LIMITS = {
  memory_mb: 512,
  timeout_seconds: 900,
  network: { level: "isolated" as const, hosts: [] },
  hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
};

describe("parseCommand / formatCommand (spec §3: command is kind 3's Cmd)", () => {
  it("splits on whitespace and honours quotes and escapes", () => {
    expect(parseCommand("tool --run")).toEqual({ command: ["tool", "--run"], error: null });
    expect(parseCommand(`tool 'a b' "c \\"d\\"" e\\ f`)).toEqual({
      command: ["tool", "a b", 'c "d"', "e f"],
      error: null,
    });
    expect(parseCommand("  ")).toEqual({ command: null, error: null });
  });

  it("refuses what the write gate would refuse", () => {
    expect(parseCommand("tool 'unterminated").error).toMatch(/Unterminated single quote/);
    expect(parseCommand('tool "x').error).toMatch(/Unterminated double quote/);
    expect(parseCommand("tool '' x").error).toMatch(/non-blank/);
    expect(parseCommand(Array.from({ length: 65 }, (_, i) => `a${i}`).join(" ")).error).toMatch(/at most 64/);
  });

  it("round-trips through formatCommand", () => {
    const command = ["tool", "--run", "a b", "it's", "x=1", ""];
    const safe = command.filter((c) => c !== "");
    expect(parseCommand(formatCommand(safe)).command).toEqual(safe);
    expect(formatCommand(["tool", "a b"])).toBe("tool 'a b'");
  });
});

describe("runtimeFormFromRevision (the current revision syncs into the form)", () => {
  it("defaults when nothing is deployed or the runtime is unreadable", () => {
    expect(runtimeFormFromRevision(null)).toEqual(DEFAULT_RUNTIME_FORM);
    expect(runtimeFormFromRevision({ kind: "nonsense" })).toEqual(DEFAULT_RUNTIME_FORM);
  });

  it("keeps a platform alias", () => {
    expect(runtimeFormFromRevision({ kind: "inline_python", runtime_image: "stactools" })).toEqual({
      ...DEFAULT_RUNTIME_FORM,
      runtimeImage: "stactools",
    });
  });

  it("reads a user image snapshot and a container command", () => {
    expect(runtimeFormFromRevision({ kind: "inline_python_on_image", image: SNAP })).toEqual({
      kind: "inline_python_on_image",
      runtimeImage: "default",
      image: SNAP,
      commandText: "",
    });
    expect(
      runtimeFormFromRevision({ kind: "container", image: SNAP, command: ["tool", "a b"] }),
    ).toEqual({ kind: "container", runtimeImage: "default", image: SNAP, commandText: "tool 'a b'" });
  });
});

describe("buildRuntimePayload", () => {
  it("builds each kind in the shape the write gate accepts", () => {
    const inline = buildRuntimePayload({ ...DEFAULT_RUNTIME_FORM, runtimeImage: "stactools" }, LIMITS);
    const onImage = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "inline_python_on_image", image: SNAP },
      LIMITS,
    );
    const container = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "container", image: SNAP, commandText: "tool --run" },
      LIMITS,
    );
    const noCommand = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "container", image: SNAP },
      LIMITS,
    );
    for (const [payload, code] of [
      [inline, "print(1)"],
      [onImage, "print(1)"],
      [container, null],
      [noCommand, null],
    ] as const) {
      if (!payload.ok) throw new Error(payload.error);
      expect(processRevisionCreateSchema.safeParse({ runtime: payload.runtime, code, env: [] }).success).toBe(true);
    }
    expect(inline.ok && inline.runtime).toMatchObject({ kind: "inline_python", image: null, runtime_image: "stactools" });
    expect(onImage.ok && onImage.carriesCode).toBe(true);
    expect(onImage.ok && onImage.runtime).toMatchObject({ kind: "inline_python_on_image", image: SNAP, runtime_image: null });
    expect(container.ok && container.carriesCode).toBe(false);
    expect(container.ok && container.runtime).toMatchObject({ kind: "container", command: ["tool", "--run"] });
    expect(noCommand.ok && noCommand.runtime).toMatchObject({ kind: "container", command: null });
  });

  it("ignores a leftover image/commandText from a previous kind when the form switches back to kind 1 (regression)", () => {
    const payload = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, image: SNAP, commandText: "x" },
      LIMITS,
    );
    if (!payload.ok) throw new Error(payload.error);
    expect(payload.runtime).toMatchObject({ kind: "inline_python", image: null });
    expect("command" in payload.runtime).toBe(false);
    expect(payload.carriesCode).toBe(true);
  });

  it("refuses a user-image kind with no image, or a broken command", () => {
    expect(buildRuntimePayload({ ...DEFAULT_RUNTIME_FORM, kind: "container" }, LIMITS)).toEqual({
      ok: false,
      error: "Choose an approved image",
    });
    const broken = buildRuntimePayload(
      { ...DEFAULT_RUNTIME_FORM, kind: "container", image: SNAP, commandText: "tool 'x" },
      LIMITS,
    );
    expect(broken.ok).toBe(false);
  });
});

describe("buildRuntimePayload hardware (K-2)", () => {
  it("carries the form's hardware and memory on every kind", () => {
    const limits = { ...LIMITS, memory_mb: 8192, hardware: { profile: "cpu-large", cpu: 4, gpu_count: 0 } };
    for (const form of [
      DEFAULT_RUNTIME_FORM,
      { ...DEFAULT_RUNTIME_FORM, kind: "inline_python_on_image" as const, image: SNAP },
      { ...DEFAULT_RUNTIME_FORM, kind: "container" as const, image: SNAP },
    ]) {
      const payload = buildRuntimePayload(form, limits);
      if (!payload.ok) throw new Error(payload.error);
      expect(payload.runtime.hardware).toEqual({ profile: "cpu-large", cpu: 4, gpu_count: 0 });
      expect(payload.runtime.memory_mb).toBe(8192);
    }
  });
});

describe("resourcesFromRevision (K-2: hardware, memory and timeout sync into the form)", () => {
  it("is the schema defaults with no revision, the timeout by process kind", () => {
    expect(resourcesFromRevision(null, "transform")).toEqual({
      hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
      memoryMb: 512,
      timeoutSeconds: 900,
    });
    expect(resourcesFromRevision(null, "extractor").timeoutSeconds).toBe(120);
  });

  it("reads what the revision deployed", () => {
    expect(
      resourcesFromRevision(
        {
          kind: "inline_python",
          memory_mb: 8192,
          timeout_seconds: 3600,
          hardware: { profile: "cpu-large", cpu: 4, gpu_count: 0 },
        },
        "transform",
      ),
    ).toEqual({
      hardware: { profile: "cpu-large", cpu: 4, gpu_count: 0 },
      memoryMb: 8192,
      timeoutSeconds: 3600,
    });
  });

  it("reads a revision stored before K-1 as standard, keeping its memory and timeout", () => {
    expect(
      resourcesFromRevision({ kind: "inline_python", memory_mb: 2048, timeout_seconds: 60 }, "extractor"),
    ).toEqual({
      hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
      memoryMb: 2048,
      timeoutSeconds: 60,
    });
  });

  it("falls back field by field on values the write gate would never have stored", () => {
    expect(
      resourcesFromRevision(
        { memory_mb: "lots", timeout_seconds: -1, hardware: { profile: "Not An Id", cpu: 1 } },
        "transform",
      ),
    ).toEqual({
      hardware: { profile: "standard", cpu: 1, gpu_count: 0 },
      memoryMb: 512,
      timeoutSeconds: 900,
    });
  });
});

describe("revisionImageDigest (run rows)", () => {
  it("reads the pinned digest of a user-image revision only", () => {
    expect(revisionImageDigest({ kind: "container", image: SNAP })).toBe(SNAP.digest);
    expect(revisionImageDigest({ kind: "inline_python", image: null })).toBeNull();
  });
});
