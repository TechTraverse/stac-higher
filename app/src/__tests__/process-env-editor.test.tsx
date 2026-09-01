import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { Connection } from "@/lib/connections/types";
import { EnvEditor } from "@/components/processes/EnvEditor";
import type { ProcessEnv } from "@/lib/processes/schemas";

/**
 * The §5.6 `env` editor (M3-W-2). The contract it has to keep honest is the
 * one the golden fixture pins: an entry is EITHER a literal or a `secret_ref`,
 * never both and never neither — so the interesting behavior here is what
 * happens at the moment an operator switches a row between the two.
 *
 * The second invariant is scoping: a `secret_ref` may only point at a
 * connection owned by the PROCESS's group, and the picker is where an
 * operator finds out — before the deploy route refuses it.
 */

const GROUP = "earth-observation";
const OTHER = "other-group";

function connection(overrides: Partial<Connection> = {}): Connection {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Prod S3",
    description: "",
    protocol: "s3",
    config: {},
    credentials_set: true,
    host_key: null,
    group_id: GROUP,
    created_by: "user-1",
    created_at: "2026-08-30T00:00:00.000Z",
    updated_at: "2026-08-30T00:00:00.000Z",
    enabled: true,
    status: "ok",
    last_checked_at: null,
    last_error: null,
    ...overrides,
  };
}

function setup(value: ProcessEnv, connections: Connection[] = [connection()]) {
  const onChange = vi.fn();
  render(
    <EnvEditor
      value={value}
      onChange={onChange}
      connections={connections}
      groupId={GROUP}
    />,
  );
  return onChange;
}

const LITERAL: ProcessEnv = [{ name: "TILE_SIZE", value: "512" }];
const SECRET: ProcessEnv = [
  {
    name: "SOURCE_TOKEN",
    secret_ref: {
      connection_id: "11111111-1111-4111-8111-111111111111",
      key: "secret_access_key",
    },
  },
];

describe("EnvEditor rows", () => {
  it("renders a literal entry's name and value", () => {
    setup(LITERAL);
    expect(screen.getByLabelText("Variable name")).toHaveValue("TILE_SIZE");
    expect(screen.getByLabelText("Variable value")).toHaveValue("512");
  });

  it("says so when there is nothing configured rather than showing an empty grid", () => {
    setup([]);
    expect(screen.getByText(/no environment variables/i)).toBeInTheDocument();
  });

  it("adds an empty literal row", () => {
    const onChange = setup([]);
    fireEvent.click(screen.getByRole("button", { name: /add variable/i }));
    expect(onChange).toHaveBeenCalledWith([{ name: "", value: "" }]);
  });

  it("removes the row it was asked to remove", () => {
    const onChange = setup([
      { name: "A", value: "1" },
      { name: "B", value: "2" },
    ]);
    fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    expect(onChange).toHaveBeenCalledWith([{ name: "B", value: "2" }]);
  });
});

describe("switching a row between a literal and a secret", () => {
  it("drops the literal value when a row becomes a secret reference", () => {
    // Carrying the old `value` alongside the new ref is the fixture's
    // "never a precedence rule — always broken" case, and it would ALSO
    // leave a plaintext value sitting where a secret was intended.
    const onChange = setup(LITERAL);
    fireEvent.change(screen.getByLabelText("Variable kind"), {
      target: { value: "secret" },
    });
    const [entry] = onChange.mock.calls[0][0];
    expect(entry).not.toHaveProperty("value");
    expect(entry.secret_ref).toEqual({ connection_id: "", key: "" });
  });

  it("drops the secret reference when a row becomes a literal", () => {
    const onChange = setup(SECRET);
    fireEvent.change(screen.getByLabelText("Variable kind"), {
      target: { value: "literal" },
    });
    const [entry] = onChange.mock.calls[0][0];
    expect(entry).not.toHaveProperty("secret_ref");
    expect(entry.value).toBe("");
  });
});

describe("the secret-ref picker", () => {
  it("offers only connections owned by the process's group", () => {
    setup(SECRET, [
      connection({ name: "Ours" }),
      connection({
        id: "22222222-2222-4222-8222-222222222222",
        name: "Theirs",
        group_id: OTHER,
      }),
    ]);
    const picker = screen.getByLabelText("Secret connection");
    const names = Array.from(picker.querySelectorAll("option")).map(
      (o) => o.textContent,
    );
    expect(names).toContain("Ours");
    expect(names).not.toContain("Theirs");
  });

  it("offers the credential keys of the selected connection's protocol", () => {
    setup(SECRET);
    const keys = Array.from(
      screen.getByLabelText("Secret key").querySelectorAll("option"),
    ).map((o) => o.getAttribute("value"));
    // s3 credentials, per connections/schemas.ts
    expect(keys).toEqual(
      expect.arrayContaining([
        "access_key_id",
        "secret_access_key",
        "session_token",
      ]),
    );
    expect(keys).not.toContain("password");
  });

  it("clears a key the newly-picked connection's protocol does not have", () => {
    // Otherwise the row keeps naming `secret_access_key` on an sftp
    // connection — a ref that only fails at run launch, hours later.
    const onChange = setup(SECRET, [
      connection(),
      connection({
        id: "33333333-3333-4333-8333-333333333333",
        name: "Drop SFTP",
        protocol: "sftp",
      }),
    ]);
    fireEvent.change(screen.getByLabelText("Secret connection"), {
      target: { value: "33333333-3333-4333-8333-333333333333" },
    });
    const [entry] = onChange.mock.calls[0][0];
    expect(entry.secret_ref).toEqual({
      connection_id: "33333333-3333-4333-8333-333333333333",
      key: "",
    });
  });

  it("keeps a key both protocols share", () => {
    const onChange = setup(
      [
        {
          name: "T",
          secret_ref: {
            connection_id: "33333333-3333-4333-8333-333333333333",
            key: "password",
          },
        },
      ],
      [
        connection({
          id: "33333333-3333-4333-8333-333333333333",
          protocol: "sftp",
        }),
        connection({
          id: "44444444-4444-4444-8444-444444444444",
          protocol: "ftp",
        }),
      ],
    );
    fireEvent.change(screen.getByLabelText("Secret connection"), {
      target: { value: "44444444-4444-4444-8444-444444444444" },
    });
    const [entry] = onChange.mock.calls[0][0];
    expect(entry.secret_ref.key).toBe("password");
  });

  it("tells the operator when their group owns no connections to point at", () => {
    setup(SECRET, []);
    expect(
      screen.getByText(/no connections in this process's group/i),
    ).toBeInTheDocument();
  });
});

describe("read-only operators", () => {
  it("disables every control", () => {
    render(
      <EnvEditor
        value={LITERAL}
        onChange={vi.fn()}
        connections={[connection()]}
        groupId={GROUP}
        disabled
      />,
    );
    expect(screen.getByLabelText("Variable name")).toBeDisabled();
    expect(screen.getByLabelText("Variable value")).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: /add variable/i }),
    ).not.toBeInTheDocument();
  });
});
