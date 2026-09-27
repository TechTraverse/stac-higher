import { describe, expect, it } from "vitest";
import { formatCommand, parseCommand } from "@/lib/processes/command";
import {
  DEFAULT_RUNTIME_FORM,
  buildRuntimePayload,
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

describe("revisionImageDigest (run rows)", () => {
  it("reads the pinned digest of a user-image revision only", () => {
    expect(revisionImageDigest({ kind: "container", image: SNAP })).toBe(SNAP.digest);
    expect(revisionImageDigest({ kind: "inline_python", image: null })).toBeNull();
  });
});
