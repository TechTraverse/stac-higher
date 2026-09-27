/**
 * C-3 image components: the pure helpers (digest, config warning, findings
 * order, picker usability) and the Add-image dialog (normalized preview,
 * credential filtering, submit -> live scan status). Query hooks are
 * mocked: what's under test is the render logic.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Image, ImageScan } from "@/lib/images/types";

const { addMutate, scanState } = vi.hoisted(() => ({
  addMutate: vi.fn(),
  scanState: { data: undefined as unknown },
}));

vi.mock("@/lib/images/queries", () => ({
  useAddImage: () => ({ mutateAsync: addMutate, isPending: false }),
  useImageScan: (imageId: string | null) => ({ data: imageId ? scanState.data : undefined }),
  useImagePolicy: () => ({ data: { allowed_registries: ["docker.io", "ghcr.io"] } }),
}));
vi.mock("@/lib/connections/queries", () => ({
  useConnections: () => ({
    data: [
      { id: "c-eo", name: "ghcr robot", protocol: "registry", config: { host: "ghcr.io" }, group_id: "earth-observation" },
      { id: "c-wx", name: "weather hub", protocol: "registry", config: { host: "docker.io" }, group_id: "weather" },
      { id: "c-s3", name: "bucket", protocol: "s3", config: { bucket: "b" }, group_id: "earth-observation" },
    ],
  }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import {
  configWarning,
  dbAgeDays,
  latestScanResult,
  shortDigest,
  sortFindings,
} from "@/components/images/format";
import { imagePickerOptions, imageUsableReason } from "@/components/images/picker";
import { ImageStatusBadge } from "@/components/images/ImageStatusBadge";
import { AddImageDialog } from "@/components/images/AddImageDialog";
import { ImagePicker } from "@/components/images/ImagePicker";
import { SeverityStack } from "@/components/images/SeverityStack";

const GROUP = "earth-observation";
const DIGEST = "sha256:" + "a".repeat(64);

function image(overrides: Partial<Image> = {}): Image {
  return {
    id: "img-1",
    reference: "ghcr.io/org/satpy-runtime",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    verdict: { pass: true, reasons: [] },
    size_bytes: 1,
    config: { user: "10001" },
    last_scan_id: "s-1",
    last_scanned_at: "2026-09-26T00:00:00.000Z",
    stale: false,
    db_built_at: null,
    added_by: "user-1",
    created_at: "2026-09-20T00:00:00.000Z",
    updated_at: "2026-09-20T00:00:00.000Z",
    exception: null,
    tag_current_digest: null,
    tag_checked_at: null,
    drifted: false,
    registry_connection: null,
    in_use_by: 0,
    ...overrides,
  };
}

const SCAN_DOC = {
  version: 1,
  kind: "admission",
  reference: "ghcr.io/org/satpy-runtime",
  tag: "1.4.2",
  error: null,
  digest: DIGEST,
  platform_digest: "sha256:" + "b".repeat(64),
  platform: { os: "linux", architecture: "amd64" },
  size_bytes: 1,
  config: { user: "", entrypoint: null, cmd: null },
  scanner: { syft: "1.0.0", grype: "0.90.0", db_built_at: "2026-09-20T06:00:00Z" },
  sbom_ref: "scans/i/s/sbom.syft.json",
  findings_ref: "scans/i/s/findings.grype.json",
  counts: { critical: 0, high: 2, medium: 0, low: 0, negligible: 0, unknown: 0 },
  fixed_counts: { critical: 0, high: 1, medium: 0, low: 0, negligible: 0, unknown: 0 },
  kev: ["CVE-2026-2"],
  max_risk: 0.9,
  top: [
    { id: "CVE-2026-1", severity: "high", package: "libxml2", version: "2.12.7", fixed_in: "2.12.9", kev: false, epss: 0.3, risk: 0.9, published_at: "2026-07-01" },
    { id: "CVE-2026-2", severity: "high", package: "openssl", version: "3.0.1", fixed_in: null, kev: true, epss: 0.1, risk: 0.2, published_at: "2026-06-01" },
  ],
  verdict: { pass: false, reasons: ["kev:CVE-2026-2"] },
  diff: null,
};

function scan(overrides: Partial<ImageScan> = {}): ImageScan {
  return {
    id: "s-1",
    image_id: "img-1",
    kind: "admission",
    status: "done",
    requested_by: "user-1",
    requested_at: "2026-09-26T00:00:00.000Z",
    started_at: null,
    finished_at: null,
    result: SCAN_DOC,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

beforeEach(() => {
  addMutate.mockReset();
  scanState.data = undefined;
});

describe("format helpers", () => {
  it("shortens a digest to sha256 plus 12 hex characters", () => {
    expect(shortDigest(DIGEST)).toBe("sha256:aaaaaaaaaaaa");
    expect(shortDigest(null)).toBe("unresolved");
  });

  it("warns when the image's USER is not the platform's uid (spec §3.2)", () => {
    expect(configWarning({ user: "" })).toBe("image declares no USER (runs as root by default); runs as 10001");
    expect(configWarning({ user: "root" })).toBe("image declares USER root; runs as 10001");
    expect(configWarning({ user: "0:0" })).toBe("image declares USER 0:0; runs as 10001");
    expect(configWarning({ user: "app" })).toMatch(/USER app; runs as 10001, so the files it needs/);
    expect(configWarning({ user: "10001:10001" })).toBeNull();
    expect(configWarning(null)).toBeNull();
  });

  it("orders findings KEV first, then by risk", () => {
    expect(sortFindings(SCAN_DOC.top as never).map((f) => f.id)).toEqual(["CVE-2026-2", "CVE-2026-1"]);
  });

  it("reads the target scan's §6.4 document: by last_scan_id, else the newest done one", () => {
    const byId = latestScanResult(
      [
        scan({ id: "s-3", status: "pending", result: null }),
        scan({ id: "s-2", status: "failed", result: { error: "pull denied" } }),
        scan({ id: "s-1" }),
      ],
      "s-1",
    );
    expect(byId.status).toBe("ok");
    expect(byId.status === "ok" && byId.result.top).toHaveLength(2);

    const byNewestDone = latestScanResult(
      [scan({ id: "s-2", status: "failed", result: null }), scan({ id: "s-1" })],
      null,
    );
    expect(byNewestDone.status).toBe("ok");

    expect(latestScanResult([], null)).toEqual({ status: "none" });
  });

  it("never falls back to an older scan when the target one is unreadable (bug fix)", () => {
    // The NEWEST done scan (s-2, named by last_scan_id) is malformed; an
    // OLDER done scan (s-1) parses fine. The reader must still say
    // "unreadable", never silently show s-1's findings.
    const result = latestScanResult(
      [scan({ id: "s-2", result: { nonsense: true } }), scan({ id: "s-1" })],
      "s-2",
    );
    expect(result).toEqual({ status: "unreadable" });
  });

  it("computes the scanner DB's age in whole days", () => {
    expect(dbAgeDays("2026-09-18T06:00:00Z", new Date("2026-09-27T12:00:00Z"))).toBe(9);
    expect(dbAgeDays(null, new Date())).toBeNull();
  });

  it("never reads an unparseable scan time as fresh (guards NaN)", () => {
    expect(dbAgeDays("not-a-date", new Date())).toBeNull();
    expect(dbAgeDays("", new Date())).toBeNull();
  });
});

describe("picker options", () => {
  it("offers only approved, fresh images this group may use", () => {
    expect(imageUsableReason(image(), GROUP)).toBeNull();
    expect(imageUsableReason(image({ status: "flagged" }), GROUP)).toBe("Flagged");
    expect(imageUsableReason(image({ status: "pending", digest: null }), GROUP)).toBe("Waiting for scan");
    expect(imageUsableReason(image({ stale: true }), GROUP)).toBe("Stale: rescan before deploying");
    expect(imageUsableReason(image({ stale: null }), GROUP)).toBe("Image policy unavailable");
    expect(
      imageUsableReason(
        image({ registry_connection: { id: "c", name: "robot", group_id: "weather", deleted: false } }),
        GROUP,
      ),
    ).toBe("Pulled with another group's registry credential");
    expect(
      imageUsableReason(
        image({ registry_connection: { id: "c", name: "robot", group_id: GROUP, deleted: true } }),
        GROUP,
      ),
    ).toBe("Registry credential was deleted");
  });

  it("says an expired exception without a passing verdict is not usable (controller ruling C-3)", () => {
    const now = new Date("2026-09-27T12:00:00.000Z");
    const expired = new Date(now.getTime() - 86_400_000).toISOString();
    const live = new Date(now.getTime() + 86_400_000).toISOString();
    expect(
      imageUsableReason(
        image({
          exception: { reason: "r", by: "admin-1", at: expired, expires_at: expired },
          verdict: { pass: false, reasons: [] },
        }),
        GROUP,
        now,
      ),
    ).toBe("Exception expired");
    expect(
      imageUsableReason(
        image({
          exception: { reason: "r", by: "admin-1", at: expired, expires_at: expired },
          verdict: { pass: true, reasons: [] },
        }),
        GROUP,
        now,
      ),
    ).toBeNull();
    expect(
      imageUsableReason(
        image({
          exception: { reason: "r", by: "admin-1", at: expired, expires_at: live },
          verdict: { pass: false, reasons: [] },
        }),
        GROUP,
        now,
      ),
    ).toBeNull();
  });

  it("hides revoked images, filters by search, and lists usable ones first", () => {
    const options = imagePickerOptions(
      [
        image({ id: "a", reference: "ghcr.io/org/zeta", status: "flagged" }),
        image({ id: "b", reference: "ghcr.io/org/alpha" }),
        image({ id: "c", reference: "ghcr.io/org/gone", status: "revoked" }),
      ],
      GROUP,
    );
    expect(options.map((o) => o.id)).toEqual(["b", "a"]);
    expect(options[0].snapshot).toEqual({ id: "b", reference: "ghcr.io/org/alpha", digest: DIGEST });
    expect(options[1].snapshot).toBeNull();
    expect(imagePickerOptions([image({ id: "b", reference: "ghcr.io/org/alpha" })], GROUP, "ZETA")).toEqual([]);
    expect(imagePickerOptions([image({ tag_at_add: "V2" })], GROUP, "v2")).toHaveLength(1);
  });
});

describe("ImageStatusBadge", () => {
  it("labels from IMAGE_STATUS_LABEL and adds stale and credential-deleted marks", () => {
    render(<ImageStatusBadge status="scan_failed" stale credentialDeleted />);
    expect(screen.getByText("Scan failed")).toBeInTheDocument();
    expect(screen.getByText("Stale")).toBeInTheDocument();
    expect(screen.getByText("Credential deleted")).toBeInTheDocument();
  });
});

describe("AddImageDialog", () => {
  function open(props: Partial<Parameters<typeof AddImageDialog>[0]> = {}) {
    render(<AddImageDialog open onOpenChange={() => {}} {...props} />);
  }

  it("previews the stored form of what is typed", () => {
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "python:3.12-slim" } });
    expect(screen.getByText("docker.io/library/python:3.12-slim")).toBeInTheDocument();
    expect(screen.getByText(/Allowed registries: docker\.io, ghcr\.io/)).toBeInTheDocument();
  });

  it("explains a reference it cannot store and disables submit", () => {
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), {
      target: { value: "python@sha256:" + "a".repeat(64) },
    });
    expect(screen.getByText(/by tag, not by digest/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add and scan/ })).toBeDisabled();
  });

  it("lists only registry credentials, only the given group's", () => {
    open({ groupId: GROUP });
    const select = screen.getByLabelText("Registry credential") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "None: a public image",
      "ghcr robot (ghcr.io)",
    ]);
  });

  it("submits, then shows the scan waiting when no scanner has picked it up", async () => {
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = {
      scan: scan({ status: "pending", result: null }),
      image: image({ status: "pending", digest: null, verdict: null }),
    };
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "python" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    await waitFor(() =>
      expect(addMutate).toHaveBeenCalledWith({
        reference: "python",
        tag: undefined,
        registry_connection_id: null,
      }),
    );
    expect(await screen.findByText(/Queued for scanning/)).toBeInTheDocument();
    expect(screen.getByText("Waiting for scan")).toBeInTheDocument();
  });

  it("never sends a whitespace-only tag: it is trimmed away like an empty one", async () => {
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = {
      scan: scan({ status: "pending", result: null }),
      image: image({ status: "pending", digest: null, verdict: null }),
    };
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "python" } });
    fireEvent.change(screen.getByLabelText("Tag (optional)"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    await waitFor(() =>
      expect(addMutate).toHaveBeenCalledWith({
        reference: "python",
        tag: undefined,
        registry_connection_id: null,
      }),
    );
  });

  it("shows the policy reasons verbatim when the scan rejects the image", async () => {
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = {
      scan: scan(),
      image: image({ status: "rejected", verdict: { pass: false, reasons: ["kev:CVE-2026-2"] } }),
    };
    open();
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "ghcr.io/org/img" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    expect(await screen.findByText("kev:CVE-2026-2")).toBeInTheDocument();
    expect(screen.getByText(/ask an admin for an exception/)).toBeInTheDocument();
  });

  it("offers the approved image back to the caller", async () => {
    const onUse = vi.fn();
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = { scan: scan(), image: image() };
    open({ onUse });
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "ghcr.io/org/img" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Use this image" }));
    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ id: "img-1", digest: DIGEST }));
  });

  it("withholds 'Use this image' for an approved image the group cannot actually use, and shows why (§4 Fix)", async () => {
    const onUse = vi.fn();
    addMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-1" });
    scanState.data = {
      scan: scan(),
      image: image({
        exception: {
          reason: "vendor fix pending",
          by: "admin-1",
          at: "2026-08-01T00:00:00.000Z",
          expires_at: "2026-08-01T00:00:00.000Z",
        },
        verdict: { pass: false, reasons: [] },
      }),
    };
    open({ onUse, groupId: GROUP });
    fireEvent.change(screen.getByLabelText("Image reference"), { target: { value: "ghcr.io/org/img" } });
    fireEvent.click(screen.getByRole("button", { name: /Add and scan/ }));
    expect(await screen.findByText("Exception expired")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use this image" })).toBeNull();
  });
});

describe("SeverityStack (a11y)", () => {
  it("gives each severity count an accessible label", () => {
    render(
      <SeverityStack
        verdict={{ pass: false, reasons: [], counts: { critical: 1, high: 2, medium: 0, low: 0 }, kev: ["CVE-2026-1"] }}
      />,
    );
    expect(screen.getByLabelText("Critical: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("High: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Medium: 0")).toBeInTheDocument();
    expect(screen.getByLabelText("Low: 0")).toBeInTheDocument();
  });
});

describe("ImagePicker (the current revision's image stays visible when it drops off the list)", () => {
  const SELECTED = { id: "img-1", reference: image().reference, digest: DIGEST };

  it("labels the current selection 'hidden by the filter' when a search hides it but it is still listed", () => {
    render(
      <ImagePicker
        images={[image()]}
        groupId={GROUP}
        value={SELECTED}
        onChange={() => {}}
        onAdd={() => {}}
      />,
    );
    fireEvent.change(screen.getByLabelText("Find an image"), { target: { value: "no-such-match" } });
    expect(screen.getByText(/selected; hidden by the filter/)).toBeInTheDocument();
  });

  it("labels the current selection 'no longer listed' when it has genuinely dropped out (e.g. revoked)", () => {
    render(
      <ImagePicker
        images={[image({ status: "revoked" })]}
        groupId={GROUP}
        value={SELECTED}
        onChange={() => {}}
        onAdd={() => {}}
      />,
    );
    expect(screen.getByText(/no longer listed; not selectable/)).toBeInTheDocument();
  });
});
