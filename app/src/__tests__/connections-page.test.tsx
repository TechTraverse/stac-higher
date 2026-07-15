import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Connection } from "@/lib/connections/types";

const { useConnectionsMock } = vi.hoisted(() => ({
  useConnectionsMock: vi.fn(),
}));

vi.mock("@/components/layout/Header", () => ({ Header: () => null }));
vi.mock("@/lib/query/auth", () => ({
  useAuthMe: () => ({
    data: { authenticated: true, mode: "bypass", identity: { groups: ["g1"] } },
  }),
}));
vi.mock("@/lib/connections/queries", () => ({
  useConnections: () => useConnectionsMock(),
  useDeleteConnection: () => ({ mutate: vi.fn(), isPending: false }),
  useResetHostKey: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/lib/connections/api", () => ({ runConnectionTest: vi.fn() }));

import { ConnectionsPage } from "@/components/connections/ConnectionsPage";

beforeAll(() => {
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

function sshConnection(): Connection {
  return {
    id: "c-ssh",
    name: "Prod SFTP",
    description: "nightly drop",
    protocol: "sftp",
    config: { host: "sftp.example.com", port: 22, root_path: "/" },
    credentials_set: true,
    host_key: { fingerprint: "SHA256:abc123", pinned_at: "2026-01-01T00:00:00Z" },
    group_id: "g1",
    created_by: "u1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    enabled: true,
    status: "ok",
    last_checked_at: "2026-01-02T00:00:00Z",
    last_error: null,
  };
}

describe("ConnectionsPage", () => {
  it("renders the empty state when there are no connections", () => {
    useConnectionsMock.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    });
    render(<ConnectionsPage />);
    expect(screen.getByText("No connections yet")).toBeInTheDocument();
  });

  it("renders status badge, protocol, credentials indicator, host-key + ssh reset action", () => {
    useConnectionsMock.mockReturnValue({
      data: [sshConnection()],
      isLoading: false,
      isError: false,
    });
    render(<ConnectionsPage />);

    expect(screen.getByText("Prod SFTP")).toBeInTheDocument();
    expect(screen.getByText("OK")).toBeInTheDocument();
    expect(screen.getByText("sftp")).toBeInTheDocument();
    expect(screen.getByText("Credentials set")).toBeInTheDocument();
    expect(screen.getByText("SHA256:abc123")).toBeInTheDocument();

    // Actions
    expect(screen.getByRole("button", { name: "Test" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Edit connection" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Delete connection" }),
    ).toBeInTheDocument();
    // Reset host key is ssh-family only, enabled because a pin exists.
    const reset = screen.getByRole("button", { name: /Reset host key/ });
    expect(reset).toBeInTheDocument();
    expect(reset).not.toBeDisabled();
  });

  it("hides the reset-host-key action for non-ssh protocols", () => {
    useConnectionsMock.mockReturnValue({
      data: [{ ...sshConnection(), protocol: "s3", host_key: null }],
      isLoading: false,
      isError: false,
    });
    render(<ConnectionsPage />);
    expect(
      screen.queryByRole("button", { name: /Reset host key/ }),
    ).not.toBeInTheDocument();
  });
});
