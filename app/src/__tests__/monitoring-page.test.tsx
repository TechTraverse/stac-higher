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

vi.mock("@/components/layout/Header", () => ({ Header: () => null }));
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
      // Far in the past — always "late" against a 3600s window.
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

  it("renders flow telemetry with the late hint when the window is blown", () => {
    useAlertsMock.mockReturnValue(loaded([]));
    useFlowsMock.mockReturnValue(loaded([flow()]));
    useChannelsMock.mockReturnValue(loaded([]));

    render(<MonitoringPage />);

    expect(screen.getByText("sentinel-2")).toBeTruthy();
    expect(screen.getByText(/12 files · 10 items · 2\.0 KB/)).toBeTruthy();
    expect(screen.getByText(/late · activity ≤ 3600s/)).toBeTruthy();
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
