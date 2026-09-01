import { Button, Input, Label } from "@stac-higher/shared";
import { Plus, Trash2 } from "lucide-react";
import { credentialKeysFor } from "@/lib/connections/schemas";
import type { Connection } from "@/lib/connections/types";
import type { ProcessEnv } from "@/lib/processes/schemas";

/**
 * The §5.6 `env` editor for a process deploy (M3-W-2).
 *
 * Two things this component owns, both of which would otherwise only be
 * discovered by the deploy route refusing the payload:
 *
 * 1. **An entry is a literal OR a secret reference, never both.** Switching a
 *    row's kind therefore DROPS the other arm rather than leaving it in the
 *    document — the golden fixture rejects a `value` + `secret_ref` collision
 *    outright, and a leftover `value` beside a reference is a plaintext
 *    secret nobody meant to store.
 * 2. **A `secret_ref` may only point inside the process's own group.** The
 *    picker filters to that group, so an operator who belongs to several sees
 *    only the connections this process can actually resolve at run time. The
 *    deploy route re-checks it — this is the affordance, not the enforcement.
 *
 * Plain `<select>` rather than the shadcn Select, matching the trigger-kind
 * picker on the same page: these are small, native, keyboard-complete
 * controls inside a dense row grid.
 */

type EnvEntry = ProcessEnv[number];

const emptyLiteral = (name = ""): EnvEntry => ({ name, value: "" });
const emptySecret = (name: string): EnvEntry => ({
  name,
  secret_ref: { connection_id: "", key: "" },
});

export function EnvEditor({
  value,
  onChange,
  connections,
  groupId,
  disabled = false,
}: {
  value: ProcessEnv;
  onChange: (next: ProcessEnv) => void;
  /** Every connection the caller can see; filtered to `groupId` here. */
  connections: Connection[];
  /** The PROCESS's owning group — not the caller's. */
  groupId: string;
  disabled?: boolean;
}) {
  const available = connections.filter((c) => c.group_id === groupId);

  const replace = (index: number, entry: EnvEntry) =>
    onChange(value.map((existing, i) => (i === index ? entry : existing)));

  return (
    <div className="grid gap-3">
      <div className="grid gap-1">
        <Label>Environment</Label>
        <p className="text-sm text-muted-foreground">
          Literal values are for non-secret configuration — they are stored in
          the revision as written. A secret reference names a key inside one of
          this group's connection credentials; it is resolved in the run
          container at launch and never read back through the API.
        </p>
      </div>

      {value.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No environment variables. Runs see only the platform's own run-scoped
          storage credentials.
        </p>
      )}

      {value.map((entry, index) => {
        const isSecret = entry.secret_ref !== undefined;
        const selected = available.find(
          (c) => c.id === entry.secret_ref?.connection_id,
        );
        const keys = selected ? credentialKeysFor(selected.protocol) : [];
        return (
          <div
            key={index}
            className="grid gap-2 rounded-md border border-border p-3 sm:grid-cols-[1fr_auto_2fr_auto] sm:items-end"
          >
            <div className="grid gap-1">
              <Label htmlFor={`env-name-${index}`}>Name</Label>
              <Input
                id={`env-name-${index}`}
                aria-label="Variable name"
                value={entry.name}
                disabled={disabled}
                placeholder="TILE_SIZE"
                className="font-mono"
                onChange={(e) => replace(index, { ...entry, name: e.target.value })}
              />
            </div>

            <div className="grid gap-1">
              <Label htmlFor={`env-kind-${index}`}>Kind</Label>
              <select
                id={`env-kind-${index}`}
                aria-label="Variable kind"
                className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                value={isSecret ? "secret" : "literal"}
                disabled={disabled}
                onChange={(e) =>
                  // Rebuild the entry from the name alone: carrying the old
                  // arm across would produce the both-arms document the
                  // contract rejects.
                  replace(
                    index,
                    e.target.value === "secret"
                      ? emptySecret(entry.name)
                      : emptyLiteral(entry.name),
                  )
                }
              >
                <option value="literal">Value</option>
                <option value="secret">Secret</option>
              </select>
            </div>

            {isSecret ? (
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="grid gap-1">
                  <Label htmlFor={`env-conn-${index}`}>Connection</Label>
                  <select
                    id={`env-conn-${index}`}
                    aria-label="Secret connection"
                    className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                    value={entry.secret_ref?.connection_id ?? ""}
                    disabled={disabled}
                    onChange={(e) => {
                      const next = available.find((c) => c.id === e.target.value);
                      const current = entry.secret_ref?.key ?? "";
                      // Keep a key both protocols share; clear one the new
                      // protocol cannot resolve, so a broken ref is visible
                      // here rather than at run launch hours later.
                      const key =
                        next && credentialKeysFor(next.protocol).includes(current)
                          ? current
                          : "";
                      replace(index, {
                        name: entry.name,
                        secret_ref: { connection_id: e.target.value, key },
                      });
                    }}
                  >
                    <option value="">Select a connection…</option>
                    {available.map((connection) => (
                      <option key={connection.id} value={connection.id}>
                        {connection.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid gap-1">
                  <Label htmlFor={`env-key-${index}`}>Credential key</Label>
                  <select
                    id={`env-key-${index}`}
                    aria-label="Secret key"
                    className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                    value={entry.secret_ref?.key ?? ""}
                    disabled={disabled || keys.length === 0}
                    onChange={(e) =>
                      replace(index, {
                        name: entry.name,
                        secret_ref: {
                          connection_id: entry.secret_ref?.connection_id ?? "",
                          key: e.target.value,
                        },
                      })
                    }
                  >
                    <option value="">Select a key…</option>
                    {keys.map((key) => (
                      <option key={key} value={key}>
                        {key}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ) : (
              <div className="grid gap-1">
                <Label htmlFor={`env-value-${index}`}>Value</Label>
                <Input
                  id={`env-value-${index}`}
                  aria-label="Variable value"
                  value={entry.value ?? ""}
                  disabled={disabled}
                  className="font-mono"
                  onChange={(e) =>
                    replace(index, { name: entry.name, value: e.target.value })
                  }
                />
              </div>
            )}

            {!disabled && (
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Remove ${entry.name || "variable"}`}
                onClick={() => onChange(value.filter((_, i) => i !== index))}
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>
        );
      })}

      {available.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No connections in this process's group — a secret reference can only
          name a key inside a connection this group owns.
        </p>
      )}

      {!disabled && (
        <div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onChange([...value, emptyLiteral()])}
          >
            <Plus className="h-4 w-4" />
            Add variable
          </Button>
        </div>
      )}
    </div>
  );
}
