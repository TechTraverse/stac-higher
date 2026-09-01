/**
 * /monitoring island (M2-D): alert rows + operator gating, flow telemetry
 * rendering (late/on-time hint), channel list with secret redaction display.
 * Query hooks are mocked; what's under test is the render logic.
 */
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Alert, Channel } from "@/lib/monitoring/api";
import type { Association } from "@/lib/associations/types";

const {
  useAlertsMock,
  useFlowsMock,
  useChannelsMock,
  markReadMutate,
} = vi.hoisted(() => ({
  useAlertsMock: vi.fn(),
  useFlowsMock: vi.fn(),
  useChannelsMock: vi.fn(),
  markReadMutate: vi.fn(),
}));

// The shell is replaced by the bare QueryProvider it wraps: these tests
// exercise page content, not the sidebar/top-bar chrome, but the components
// under test still need a QueryClient.
vi.mock("@/components/layout/AppShell", async () => {
  const { QueryProvider } = await import("@/components/layout/QueryProvider");
  return {
    AppShell: ({ children }: { children: React.ReactNode }) => (
      <QueryProvider>{children}</QueryProvider>
    ),
  };
});
vi.mock("@/lib/query/auth", () => ({
  useAuthMe: () => ({
    data: {
      authenticated: true,
      mode: "bypass",
      identity: { sub: "u1", groups: ["g1"], roles: ["operator"] },
    },
  }),
}));
vi.mock("@/lib/monitoring/queries", () => ({
  useAlerts: (state: string) => useAlertsMock(state),
  useFlows: () => useFlowsMock(),
  useChannels: () => useChannelsMock(),
  useMarkAlertsRead: () => ({ mutate: markReadMutate, isPending: false }),
  useAckAlert: () => ({ mutate: vi.fn(), isPending: false }),
  useResolveAlert: () => ({ mutate: vi.fn(), isPending: false }),
  useUnreadAlerts: () => ({ data: 0, isError: false, isLoading: false }),
  useCreateChannel: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteChannel: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { MonitoringPage } from "@/components/monitoring/MonitoringPage";

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

function loaded<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null, refetch: vi.fn() };
}

function alert(overrides: Partial<Alert> = {}): Alert {
  return {
    id: "a1",
    source: "flow",
    kind: "ingest_inactivity",
    connection_id: "c1",
    association_id: "as1",
    channel_id: null,
    state: "firing",
    message: "no ingest activity for 4000s (expected within 3600s)",
    first_seen: "2026-08-19T00:00:00.000Z",
    last_seen: "2026-08-19T01:00:00.000Z",
    acknowledged_at: null,
    acknowledged_by: null,
    resolved_at: null,
    group_id: "g1",
    connection_name: "src",
    collection_id: "sentinel-2",
    ...overrides,
  };
}

function flow(overrides: Partial<Association> = {}): Association {
  return {
    id: "as1",
    collection_id: "sentinel-2",
    connection_id: "c1",
    direction: "ingest",
    enabled: true,
    config: {},
    expectation: { expect_activity_within_seconds: 3600 },
    flow_stats: {
      files: 12,
      items: 10,
      bytes: 2048,
      failed: 0,
      last_activity_at: "2026-01-01T00:00:00.000Z",
    },
    created_by: "u1",
    created_at: "2026-08-01T00:00:00.000Z",
    updated_at: "2026-08-01T00:00:00.000Z",
    connection: { name: "src", protocol: "s3", status: "ok" },
    ...overrides,
  };
}

function channel(overrides: Partial<Channel> = {}): Channel {
  return {
    id: "ch1",
    group_id: "g1",
    kind: "webhook",
    config: { url: "https://hooks.example.com/stac", has_secret: true },
    created_by: "u1",
    created_at: "2026-08-19T00:00:00.000Z",
    updated_at: "2026-08-19T00:00:00.000Z",
    ...overrides,
  };
}

describe("MonitoringPage", () => {
  it("renders alerts with ack/resolve for operators and marks read on mount", () => {
    useAlertsMock.mockReturnValue(loaded([alert()]));
    useFlowsMock.mockReturnValue(loaded([]));
    useChannelsMock.mockReturnValue(loaded([]));

    render(<MonitoringPage />);

    expect(screen.getByText(/no ingest activity for 4000s/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /ack/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^resolve$/i })).toBeTruthy();
    expect(markReadMutate).toHaveBeenCalledTimes(1);
  });

  it("marks a flow late when the monitor holds an open breach alert for it", () => {
    // The hint is derived from the open alerts (the monitor's verdict), not
    // re-computed locally from flow_stats.
    useAlertsMock.mockReturnValue(loaded([alert()]));
    useFlowsMock.mockReturnValue(loaded([flow()]));
    useChannelsMock.mockReturnValue(loaded([]));

    render(<MonitoringPage />);

    expect(screen.getByText("sentinel-2")).toBeTruthy();
    expect(screen.getByText(/12 files · 10 items · 2\.0 KB/)).toBeTruthy();
    expect(screen.getByText(/late · activity ≤ 3600s/)).toBeTruthy();
  });

  it("marks a flow on time when no open breach alert exists for it", () => {
    // A breach alert for a DIFFERENT association must not mark this flow late.
    useAlertsMock.mockReturnValue(loaded([alert({ association_id: "other" })]));
    useFlowsMock.mockReturnValue(loaded([flow()]));
    useChannelsMock.mockReturnValue(loaded([]));

    render(<MonitoringPage />);

    expect(screen.getByText(/on time · activity ≤ 3600s/)).toBeTruthy();
  });

  it("marks a deliver flow late on an open delivery_slo alert", () => {
    // Literal kind string on purpose: it pins the cross-runtime contract
    // (pipeline MONITOR_KINDS) so a typo in EXPECTATION_BREACH_KIND fails here.
    useAlertsMock.mockReturnValue(
      loaded([
        alert({
          kind: "delivery_slo",
          message: "delivery took 900s (expected within 600s)",
        }),
      ]),
    );
    useFlowsMock.mockReturnValue(
      loaded([
        flow({
          direction: "deliver",
          expectation: { deliver_within_seconds: 600 },
        }),
      ]),
    );
    useChannelsMock.mockReturnValue(loaded([]));

    render(<MonitoringPage />);

    expect(screen.getByText(/late · deliver ≤ 600s/)).toBeTruthy();
  });

  it("withholds the verdict when the alerts query has no data", () => {
    // A failed alerts query must not read as "no open alerts" → "on time".
    useAlertsMock.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch: vi.fn(),
    });
    useFlowsMock.mockReturnValue(loaded([flow()]));
    useChannelsMock.mockReturnValue(loaded([]));

    render(<MonitoringPage />);

    expect(screen.getByText(/^activity ≤ 3600s$/)).toBeTruthy();
    expect(screen.queryByText(/on time/)).toBeNull();
    expect(screen.queryByText(/late ·/)).toBeNull();
  });

  it("lists channels with the signed badge, never the secret", () => {
    useAlertsMock.mockReturnValue(loaded([]));
    useFlowsMock.mockReturnValue(loaded([]));
    useChannelsMock.mockReturnValue(loaded([channel()]));

    const { container } = render(<MonitoringPage />);

    expect(screen.getByText("https://hooks.example.com/stac")).toBeTruthy();
    expect(screen.getByText(/signed/)).toBeTruthy();
    expect(container.textContent).not.toContain("s3cret");
    expect(screen.getByTestId("channel-add")).toBeTruthy();
  });
});
