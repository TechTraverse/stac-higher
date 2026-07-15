import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ConnectionForm } from "@/components/connections/ConnectionForm";
import { STAC_API_RESERVED_MESSAGE } from "@/lib/connections/schemas";
import type { Connection } from "@/lib/connections/types";

const { createMock, updateMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  updateMock: vi.fn(),
}));

vi.mock("@/lib/connections/queries", () => ({
  useCreateConnection: () => ({ mutate: createMock, isPending: false }),
  useUpdateConnection: () => ({ mutate: updateMock, isPending: false }),
}));

beforeAll(() => {
  globalThis.ResizeObserver =
    globalThis.ResizeObserver ||
    (class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver);
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
  Element.prototype.releasePointerCapture = vi.fn();
  Element.prototype.setPointerCapture = vi.fn();
  window.matchMedia =
    window.matchMedia ||
    ((() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })) as unknown as typeof window.matchMedia);
});

beforeEach(() => {
  createMock.mockReset();
  updateMock.mockReset();
});

function makeConnection(overrides: Partial<Connection> = {}): Connection {
  return {
    id: "conn-1",
    name: "Existing",
    description: "",
    protocol: "ftp",
    config: { host: "ftp.example.com", port: 21, root_path: "/" },
    credentials_set: true,
    host_key: null,
    group_id: "g1",
    created_by: "u1",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    enabled: true,
    status: "unverified",
    last_checked_at: null,
    last_error: null,
    ...overrides,
  };
}

describe("ConnectionForm — create", () => {
  it("renders s3 config + credential fields for the default protocol", () => {
    render(
      <ConnectionForm
        open
        onOpenChange={() => {}}
        groups={["g1"]}
      />,
    );
    // s3 config
    expect(screen.getByLabelText("Bucket")).toBeInTheDocument();
    expect(screen.getByLabelText(/Region/)).toBeInTheDocument();
    // s3 credentials (write-only)
    expect(screen.getByLabelText("Access key ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Secret access key")).toBeInTheDocument();
    // no ssh-only fields
    expect(screen.queryByLabelText("Host")).not.toBeInTheDocument();
  });

  it("blocks submit and shows validation errors for missing required fields", async () => {
    render(<ConnectionForm open onOpenChange={() => {}} groups={["g1"]} />);
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByText("Name is required")).toBeInTheDocument();
    // Required config (bucket) also fails validation → at least two field errors.
    expect(screen.getAllByRole("alert").length).toBeGreaterThanOrEqual(2);
    expect(createMock).not.toHaveBeenCalled();
  });
});

describe("ConnectionForm — stac-api reserved", () => {
  it("shows the reserved message and hides the form body", () => {
    render(
      <ConnectionForm
        open
        onOpenChange={() => {}}
        initial={makeConnection({ protocol: "stac-api", credentials_set: false })}
        groups={["g1"]}
      />,
    );
    expect(screen.getByText(STAC_API_RESERVED_MESSAGE)).toBeInTheDocument();
    // The form body (name field) must not render for a reserved protocol.
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  });
});

describe("ConnectionForm — edit (write-only credentials)", () => {
  it("shows the keep-existing hint and omits credentials when left blank", async () => {
    render(
      <ConnectionForm
        open
        onOpenChange={() => {}}
        initial={makeConnection()}
        groups={["g1"]}
      />,
    );
    expect(
      screen.getByText(/Leave blank to keep them/),
    ).toBeInTheDocument();
    // Existing secrets are never rendered.
    expect((screen.getByLabelText("Password") as HTMLInputElement).value).toBe(
      "",
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalled());
    const arg = updateMock.mock.calls[0][0];
    expect(arg.id).toBe("conn-1");
    expect(arg.input).not.toHaveProperty("credentials");
  });

  it("sends credentials only when the user enters new values", async () => {
    render(
      <ConnectionForm
        open
        onOpenChange={() => {}}
        initial={makeConnection({ credentials_set: false })}
        groups={["g1"]}
      />,
    );
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "bob" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "s3cret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalled());
    const arg = updateMock.mock.calls[0][0];
    expect(arg.input.credentials).toEqual({
      username: "bob",
      password: "s3cret",
    });
  });

  it("disables the protocol picker on edit", () => {
    render(
      <ConnectionForm
        open
        onOpenChange={() => {}}
        initial={makeConnection()}
        groups={["g1"]}
      />,
    );
    expect(screen.getByLabelText("Protocol")).toBeDisabled();
  });
});
