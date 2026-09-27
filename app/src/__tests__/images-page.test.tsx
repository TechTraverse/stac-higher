/**
 * /images island (C-3, container-images spec §9.2). Query hooks are mocked;
 * what's under test is the render logic, the operator gating, the filters
 * and the detail sheet's warnings.
 */
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { Image, ImageScan } from "@/lib/images/types";

const { useImagesMock, useImageMock, rescanMutate, grantMutate, revokeMutate, roles } = vi.hoisted(() => ({
  useImagesMock: vi.fn(),
  useImageMock: vi.fn(),
  rescanMutate: vi.fn(),
  grantMutate: vi.fn(),
  revokeMutate: vi.fn(),
  roles: { value: ["operator"] as string[] },
}));

vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => <QueryProvider>{children}</QueryProvider>,
  };
});
vi.mock("@/lib/query/auth", () => ({
  useAuthMe: () => ({
    data: {
      authenticated: true,
      mode: "bypass",
      identity: { sub: "u1", groups: ["earth-observation"], roles: roles.value },
    },
  }),
}));
vi.mock("@/lib/images/queries", () => ({
  useImages: (filters: unknown) => useImagesMock(filters),
  useImage: (id: string | null) => useImageMock(id),
  useRescanImage: () => ({ mutateAsync: rescanMutate, isPending: false }),
  useAddImage: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useImageScan: () => ({ data: undefined }),
  useImagePolicy: () => ({ data: undefined }),
  useGrantImageException: () => ({ mutateAsync: grantMutate, isPending: false }),
  useRevokeImage: () => ({ mutateAsync: revokeMutate, isPending: false }),
}));
vi.mock("@/lib/connections/queries", () => ({ useConnections: () => ({ data: [] }) }));
// F1 (controller ruling): the mocked "sonner" module replaces the whole
// package, including the `Toaster` the real `@/components/ui/sonner`
// imports and QueryProvider renders on every page. Without a stub here that
// import resolves to `undefined` and every test throws on render.
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() }, Toaster: () => null }));

import { ImagesPage } from "@/components/images/ImagesPage";

const DIGEST = "sha256:" + "a".repeat(64);

function image(overrides: Partial<Image> = {}): Image {
  return {
    id: "img-1",
    reference: "ghcr.io/org/satpy-runtime",
    tag_at_add: "1.4.2",
    digest: DIGEST,
    status: "approved",
    verdict: { pass: true, reasons: [], counts: { critical: 0, high: 3, medium: 1, low: 0 }, kev: [] },
    size_bytes: 1,
    config: { user: "10001" },
    last_scan_id: "s-1",
    last_scanned_at: new Date().toISOString(),
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
    in_use_by: 2,
    ...overrides,
  };
}

function scan(overrides: Partial<ImageScan> = {}): ImageScan {
  return {
    id: "s-1",
    image_id: "img-1",
    kind: "admission",
    status: "done",
    requested_by: "user-1",
    requested_at: "2026-09-26T00:00:00.000Z",
    started_at: null,
    finished_at: "2026-09-26T00:05:00.000Z",
    result: null,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

function list(images: Image[], scanWindowDays: number | null = 30) {
  useImagesMock.mockReturnValue({
    data: { images, scan_window_days: scanWindowDays },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
}

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as never);
});

beforeEach(() => {
  useImagesMock.mockReset();
  useImageMock.mockReset();
  useImageMock.mockReturnValue({ data: undefined, isLoading: false, error: null });
  rescanMutate.mockReset();
  grantMutate.mockReset();
  revokeMutate.mockReset();
  roles.value = ["operator"];
});

describe("ImagesPage", () => {
  it("shows the empty state with Add image for an operator", () => {
    list([]);
    render(<ImagesPage />);
    expect(screen.getByRole("heading", { name: "Images", level: 1 })).toBeInTheDocument();
    expect(screen.getByText("No images yet")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Add image" }).length).toBeGreaterThan(0);
  });

  it("renders read-only for a member", () => {
    roles.value = ["member"];
    list([]);
    render(<ImagesPage />);
    expect(screen.queryByRole("button", { name: "Add image" })).toBeNull();
  });

  it("renders one row per image with its status, marks, counts and usage", () => {
    list([
      image(),
      image({
        id: "img-2",
        reference: "docker.io/library/python",
        tag_at_add: "3.12-slim",
        status: "flagged",
        stale: true,
        drifted: true,
        in_use_by: 0,
        verdict: { pass: false, reasons: ["kev:CVE-2026-9"], counts: { critical: 1 }, kev: ["CVE-2026-9"] },
        registry_connection: { id: "c-1", name: "hub robot", group_id: "earth-observation", deleted: true },
      }),
    ]);
    render(<ImagesPage />);
    expect(screen.getByText("2 images in the registry")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const flagged = within(rows[1]);
    expect(flagged.getByText("docker.io/library/python:3.12-slim")).toBeInTheDocument();
    expect(flagged.getByText("Flagged")).toBeInTheDocument();
    expect(flagged.getByText("Stale")).toBeInTheDocument();
    expect(flagged.getByText("Credential deleted")).toBeInTheDocument();
    expect(flagged.getByText("KEV 1")).toBeInTheDocument();
    expect(flagged.getByText("tag moved")).toBeInTheDocument();
    expect(within(rows[0]).getByText("Approved")).toBeInTheDocument();
    expect(within(rows[0]).getByText("sha256:aaaaaaaaaaaa")).toBeInTheDocument();
  });

  it("passes the filters to the query", () => {
    list([]);
    render(<ImagesPage />);
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "flagged" } });
    fireEvent.click(screen.getByLabelText("In use only"));
    fireEvent.change(screen.getByLabelText("Search images"), { target: { value: "satpy" } });
    expect(useImagesMock).toHaveBeenLastCalledWith({ status: "flagged", q: "satpy", in_use: true });
  });

  it("says why staleness is unknown when the policy cannot be read (announced via role=status)", () => {
    list([image({ stale: null })], null);
    render(<ImagesPage />);
    const banner = screen.getByText(/image policy could not be read/);
    expect(banner.closest('[role="status"]')).not.toBeNull();
  });

  it("shows an expired exception as 'expired <date>', a live one as 'until <date>'", () => {
    const expires = "2026-08-01T00:00:00.000Z";
    list([
      image({
        id: "img-expired",
        exception: { reason: "r", by: "admin-1", at: expires, expires_at: expires },
        verdict: { pass: false, reasons: [] },
      }),
      image({
        id: "img-live",
        reference: "docker.io/library/python",
        exception: {
          reason: "r",
          by: "admin-1",
          at: expires,
          expires_at: new Date(Date.now() + 86_400_000).toISOString(),
        },
      }),
    ]);
    render(<ImagesPage />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText(/^expired /)).toBeInTheDocument();
    expect(within(rows[1]).getByText(/^until /)).toBeInTheDocument();
  });

  it("opens the detail sheet with the credential, config and reason warnings, and Rescan now", async () => {
    const detail = image({
      status: "rejected",
      config: { user: "" },
      verdict: { pass: false, reasons: ["critical_fixed:libxml2"], counts: { critical: 1 }, kev: [] },
      registry_connection: { id: "c-1", name: "ghcr robot", group_id: "earth-observation", deleted: true },
    });
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id
        ? {
            image: detail,
            scans: [scan()],
            in_use_by: [{ process_id: "p-1", name: "geocolor", group_id: "earth-observation" }],
            in_use_elsewhere: 1,
          }
        : undefined,
      isLoading: false,
      error: null,
    }));
    rescanMutate.mockResolvedValue({ image_id: "img-1", scan_id: "s-2", kind: "rescan" });
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));

    expect(await screen.findByTestId("credential-deleted")).toHaveTextContent(/ghcr robot/);
    expect(screen.getByTestId("config-warning")).toHaveTextContent(
      "image declares no USER (runs as root by default); runs as 10001",
    );
    expect(screen.getByText("critical_fixed:libxml2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "geocolor" })).toHaveAttribute("href", "/processes/p-1");
    expect(screen.getByText(/1 more in other groups/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Rescan now/ }).closest('[role="status"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Rescan now/ }));
    expect(rescanMutate).toHaveBeenCalledWith("img-1");
  });

  it("shows the expired-exception warning and never falls back to an older scan's findings", async () => {
    const expires = "2026-08-01T00:00:00.000Z";
    const detail = image({
      last_scan_id: "s-2",
      exception: { reason: "vendor fix pending", by: "admin-1", at: expires, expires_at: expires },
      verdict: { pass: false, reasons: [] },
    });
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id
        ? {
            image: detail,
            scans: [
              scan({ id: "s-2", result: { nonsense: true } }),
              scan({ id: "s-1", result: null }),
            ],
            in_use_by: [],
            in_use_elsewhere: 0,
          }
        : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByText(/new deploys are refused until a rescan passes/)).toBeInTheDocument();
    expect(screen.getByText("Findings for the latest scan could not be read")).toBeInTheDocument();
  });

  it("hides Rescan now from a member", async () => {
    roles.value = ["member"];
    list([image()]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id ? { image: image(), scans: [], in_use_by: [], in_use_elsewhere: 0 } : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByText("Scan history")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Rescan now/ })).toBeNull();
  });

  it("keys 'expired' on the date alone, even when the latest verdict passes (C-4 fix)", async () => {
    const past = "2026-08-01T00:00:00.000Z";
    const detail = image({
      exception: { reason: "vendor fix pending", by: "admin-1", at: past, expires_at: past },
      verdict: { pass: true, reasons: [] },
    });
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id ? { image: detail, scans: [], in_use_by: [], in_use_elsewhere: 0 } : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText(/^expired /)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByText(/Granted by admin-1, expired/)).toBeInTheDocument();
    // The verdict passes on its own: no "deploys are refused" warning.
    expect(screen.queryByText(/new deploys are refused until a rescan passes/)).toBeNull();
  });

  it("shows each rescan's diff, its drift and a findings link in the scan history", async () => {
    const detail = image();
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id
        ? {
            image: detail,
            scans: [
              scan({
                id: "s-2",
                kind: "rescan",
                finished_at: "2026-09-27T03:20:00.000Z",
                findings_ref: "scans/img-1/s-2/findings.grype.json",
                result: {
                  tag_drift: { current_digest: "sha256:" + "b".repeat(64), drifted: true },
                  diff: {
                    previous_scan_id: "s-1",
                    new: ["CVE-2026-0003"],
                    resolved: [],
                    newly_fixed: [],
                    new_kev: ["CVE-2026-0003"],
                    verdict_changed: true,
                    counts_delta: { critical: 0, high: 2, medium: -1, low: 0, negligible: 0, unknown: 0 },
                  },
                },
              }),
              scan({ id: "s-1", finished_at: "2026-09-26T00:05:00.000Z" }),
            ],
            in_use_by: [],
            in_use_elsewhere: 0,
          }
        : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: "ghcr.io/org/satpy-runtime:1.4.2" }));
    expect(await screen.findByTestId("scan-diff")).toHaveTextContent(
      "+2 high, −1 medium since 2026-09-26 · 1 new KEV, verdict changed",
    );
    expect(screen.getByText("tag moved to sha256:bbbbbbbbbbbb")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "findings" })).toHaveAttribute(
      "href",
      "/api/images/img-1/scans/s-2/findings",
    );
  });

  function openDetail(detail: Image) {
    list([detail]);
    useImageMock.mockImplementation((id: string | null) => ({
      data: id ? { image: detail, scans: [], in_use_by: [], in_use_elsewhere: 0 } : undefined,
      isLoading: false,
      error: null,
    }));
    render(<ImagesPage />);
    fireEvent.click(screen.getByRole("button", { name: `${detail.reference}:${detail.tag_at_add}` }));
  }

  it("lets an admin grant an exception on a rejected image, with a reason and a bounded expiry", async () => {
    roles.value = ["admin"];
    grantMutate.mockResolvedValue({ image: image() });
    openDetail(image({ status: "rejected", verdict: { pass: false, reasons: ["kev:CVE-2026-9"] } }));
    fireEvent.click(await screen.findByRole("button", { name: /Grant exception/ }));
    fireEvent.change(screen.getByLabelText("Reason"), {
      target: { value: "vendor fix lands next sprint" },
    });
    fireEvent.change(screen.getByLabelText("Lasts (days)"), { target: { value: "10" } });
    const before = Date.now();
    fireEvent.click(screen.getByRole("button", { name: "Save exception" }));
    await waitFor(() => expect(grantMutate).toHaveBeenCalledTimes(1));
    const [{ id, body }] = grantMutate.mock.calls[0];
    expect(id).toBe("img-1");
    expect(body.reason).toBe("vendor fix lands next sprint");
    const expires = Date.parse(body.expires_at);
    expect(expires).toBeGreaterThanOrEqual(before + 10 * 86_400_000);
    expect(expires).toBeLessThanOrEqual(Date.now() + 10 * 86_400_000);
  });

  it("refuses a thin reason before anything is sent", async () => {
    roles.value = ["admin"];
    openDetail(image({ status: "flagged" }));
    fireEvent.click(await screen.findByRole("button", { name: /Grant exception/ }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "ok" } });
    fireEvent.click(screen.getByRole("button", { name: "Save exception" }));
    expect(await screen.findByText(/At least 10 characters/)).toBeInTheDocument();
    expect(grantMutate).not.toHaveBeenCalled();
  });

  it("offers to replace the exception an approved image carries, and none when it carries none", async () => {
    roles.value = ["admin"];
    const live = new Date(Date.now() + 5 * 86_400_000).toISOString();
    openDetail(
      image({ exception: { reason: "vendor fix pending", by: "admin-1", at: live, expires_at: live } }),
    );
    expect(await screen.findByRole("button", { name: /Replace exception/ })).toBeInTheDocument();
  });

  it("an approved image without an exception offers revoke only", async () => {
    roles.value = ["admin"];
    openDetail(image());
    expect(await screen.findByRole("button", { name: /Revoke image/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Grant exception|Replace exception/ })).toBeNull();
  });

  it("revokes only after the confirmation", async () => {
    roles.value = ["admin"];
    revokeMutate.mockResolvedValue({ image: image({ status: "revoked" }) });
    openDetail(image());
    fireEvent.click(await screen.findByRole("button", { name: /Revoke image/ }));
    expect(screen.getByText(/Revoking is final/)).toHaveTextContent(/2 processes use it/);
    expect(revokeMutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(revokeMutate).toHaveBeenCalledWith("img-1"));
  });

  it("shows no admin verbs to an operator", async () => {
    openDetail(image({ status: "rejected" }));
    expect(await screen.findByText("Scan history")).toBeInTheDocument();
    expect(screen.queryByTestId("image-admin-actions")).toBeNull();
  });

  it("shows no admin verbs on a revoked image, even to an admin", async () => {
    roles.value = ["admin"];
    openDetail(image({ status: "revoked" }));
    expect(await screen.findByText("Scan history")).toBeInTheDocument();
    expect(screen.queryByTestId("image-admin-actions")).toBeNull();
  });
});
