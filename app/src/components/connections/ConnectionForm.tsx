import { useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  Button,
  Input,
  Label,
  Switch,
  Textarea,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@stac-higher/shared";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  CONNECTION_PROTOCOLS,
  WRITABLE_PROTOCOLS,
  STAC_API_RESERVED_MESSAGE,
  configSchemaFor,
  credentialsSchemaFor,
  type ConnectionProtocol,
  type WritableProtocol,
  type ConnectionCreateInput,
  type ConnectionUpdateInput,
} from "@/lib/connections/schemas";
import type { Connection } from "@/lib/connections/types";
import {
  useCreateConnection,
  useUpdateConnection,
} from "@/lib/connections/queries";

// ---------------------------------------------------------------------------
// Per-protocol field descriptors. These drive rendering; the Zod schemas from
// `schemas.ts` (configSchemaFor / credentialsSchemaFor) drive validation, so
// the two stay in lock-step with the cross-runtime contract.
// ---------------------------------------------------------------------------

type FieldType = "text" | "number" | "password" | "textarea" | "url" | "switch";

interface FieldDef {
  name: string;
  label: string;
  type: FieldType;
  placeholder?: string;
  optional?: boolean;
  help?: string;
}

const CONFIG_FIELDS: Record<WritableProtocol, FieldDef[]> = {
  s3: [
    { name: "bucket", label: "Bucket", type: "text", placeholder: "my-bucket" },
    {
      name: "region",
      label: "Region",
      type: "text",
      optional: true,
      placeholder: "us-east-1",
    },
    {
      name: "endpoint",
      label: "Endpoint",
      type: "url",
      optional: true,
      placeholder: "https://s3.example.com",
      help: "Custom S3-compatible endpoint (MinIO, R2, …).",
    },
    {
      name: "force_path_style",
      label: "Force path-style addressing",
      type: "switch",
      optional: true,
    },
  ],
  ssh: [
    { name: "host", label: "Host", type: "text", placeholder: "sftp.example.com" },
    { name: "port", label: "Port", type: "number" },
    { name: "root_path", label: "Root path", type: "text", placeholder: "/" },
  ],
  sftp: [
    { name: "host", label: "Host", type: "text", placeholder: "sftp.example.com" },
    { name: "port", label: "Port", type: "number" },
    { name: "root_path", label: "Root path", type: "text", placeholder: "/" },
  ],
  ftp: [
    { name: "host", label: "Host", type: "text", placeholder: "ftp.example.com" },
    { name: "port", label: "Port", type: "number" },
    { name: "root_path", label: "Root path", type: "text", placeholder: "/" },
  ],
  ftps: [
    { name: "host", label: "Host", type: "text", placeholder: "ftp.example.com" },
    { name: "port", label: "Port", type: "number" },
    { name: "root_path", label: "Root path", type: "text", placeholder: "/" },
    { name: "implicit", label: "Implicit TLS", type: "switch", optional: true },
  ],
};

const CRED_FIELDS: Record<WritableProtocol, FieldDef[]> = {
  s3: [
    { name: "access_key_id", label: "Access key ID", type: "text" },
    { name: "secret_access_key", label: "Secret access key", type: "password" },
    {
      name: "session_token",
      label: "Session token",
      type: "password",
      optional: true,
    },
  ],
  ssh: [
    { name: "username", label: "Username", type: "text" },
    {
      name: "password",
      label: "Password",
      type: "password",
      optional: true,
      help: "Provide a password or a private key (at least one).",
    },
    {
      name: "private_key",
      label: "Private key",
      type: "textarea",
      optional: true,
      placeholder: "-----BEGIN OPENSSH PRIVATE KEY-----",
    },
    { name: "passphrase", label: "Key passphrase", type: "password", optional: true },
  ],
  sftp: [
    { name: "username", label: "Username", type: "text" },
    {
      name: "password",
      label: "Password",
      type: "password",
      optional: true,
      help: "Provide a password or a private key (at least one).",
    },
    {
      name: "private_key",
      label: "Private key",
      type: "textarea",
      optional: true,
      placeholder: "-----BEGIN OPENSSH PRIVATE KEY-----",
    },
    { name: "passphrase", label: "Key passphrase", type: "password", optional: true },
  ],
  ftp: [
    { name: "username", label: "Username", type: "text" },
    { name: "password", label: "Password", type: "password" },
  ],
  ftps: [
    { name: "username", label: "Username", type: "text" },
    { name: "password", label: "Password", type: "password" },
  ],
};

const DEFAULT_PORT: Record<WritableProtocol, number> = {
  s3: 0,
  ssh: 22,
  sftp: 22,
  ftp: 21,
  ftps: 21,
};

function defaultConfig(protocol: WritableProtocol): Record<string, unknown> {
  switch (protocol) {
    case "s3":
      return { bucket: "", region: "", endpoint: "", force_path_style: false };
    case "ssh":
    case "sftp":
      return { host: "", port: 22, root_path: "/" };
    case "ftp":
      return { host: "", port: 21, root_path: "/" };
    case "ftps":
      return { host: "", port: 21, root_path: "/", implicit: false };
  }
}

function blankCredentials(protocol: WritableProtocol): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of CRED_FIELDS[protocol]) out[f.name] = "";
  return out;
}

/** Drop empty-string / undefined leaves — the write-only credential contract. */
function cleanRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null) return {};
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    if (v === undefined) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    out[k] = v;
  }
  return out;
}

interface FormValues {
  name: string;
  description: string;
  group_id: string;
  enabled: boolean;
  config: Record<string, unknown>;
  credentials: Record<string, unknown>;
}

function buildFormSchema(protocol: WritableProtocol, isEdit: boolean) {
  const base = {
    name: z.string().min(1, "Name is required").max(200),
    description: z.string().max(2000).optional(),
    group_id: z.string().min(1, "A group is required"),
    enabled: z.boolean(),
    config: z.preprocess(cleanRecord, configSchemaFor(protocol)),
  };

  if (!isEdit) {
    return z.object({
      ...base,
      credentials: z.preprocess(cleanRecord, credentialsSchemaFor(protocol)),
    });
  }

  // Edit: credentials are write-only. Leaving every field blank keeps the
  // stored envelope; filling any field triggers full validation + a wholesale
  // replace (partial merges do not exist).
  const credSchema = credentialsSchemaFor(protocol);
  return z
    .object({
      ...base,
      credentials: z.record(z.string(), z.unknown()).optional(),
    })
    .superRefine((data, ctx) => {
      const creds = cleanRecord(data.credentials);
      if (Object.keys(creds).length === 0) return;
      const parsed = credSchema.safeParse(creds);
      if (!parsed.success) {
        for (const issue of parsed.error.issues) {
          ctx.addIssue({ ...issue, path: ["credentials", ...issue.path] });
        }
      }
    });
}

interface ConnectionFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initial?: Connection;
  /** Groups the caller may assign a connection to. */
  groups: string[];
}

export function ConnectionForm({
  open,
  onOpenChange,
  initial,
  groups,
}: ConnectionFormProps) {
  const isEdit = !!initial;
  const [protocol, setProtocol] = useState<ConnectionProtocol>(
    initial?.protocol ?? "s3",
  );

  const reserved = protocol === "stac-api";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit Connection" : "New Connection"}
          </DialogTitle>
          <DialogDescription>
            {isEdit
              ? "Update endpoint settings. Credentials are write-only — leave them blank to keep the stored ones."
              : "Configure an endpoint the pipeline can ingest from or deliver to."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <Label htmlFor="conn-protocol">Protocol</Label>
          <Select
            value={protocol}
            onValueChange={(v) => setProtocol(v as ConnectionProtocol)}
            disabled={isEdit}
          >
            <SelectTrigger id="conn-protocol" aria-label="Protocol">
              <SelectValue placeholder="Select a protocol" />
            </SelectTrigger>
            <SelectContent>
              {CONNECTION_PROTOCOLS.map((p) => {
                const isReserved = !(
                  WRITABLE_PROTOCOLS as readonly string[]
                ).includes(p);
                return (
                  <SelectItem key={p} value={p} disabled={isReserved}>
                    {p}
                    {isReserved ? " (reserved)" : ""}
                  </SelectItem>
                );
              })}
            </SelectContent>
          </Select>
          {isEdit && (
            <p className="text-xs text-muted-foreground">
              Protocol is immutable — create a new connection to change it.
            </p>
          )}
        </div>

        {reserved ? (
          <p className="text-sm text-destructive" role="alert">
            {STAC_API_RESERVED_MESSAGE}
          </p>
        ) : (
          <ConnectionFormBody
            key={isEdit ? initial!.id : protocol}
            protocol={protocol as WritableProtocol}
            initial={initial}
            groups={groups}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

interface BodyProps {
  protocol: WritableProtocol;
  initial?: Connection;
  groups: string[];
  onDone: () => void;
}

function ConnectionFormBody({ protocol, initial, groups, onDone }: BodyProps) {
  const isEdit = !!initial;
  const createMutation = useCreateConnection();
  const updateMutation = useUpdateConnection();

  const defaultGroup =
    initial?.group_id ?? (groups.length > 0 ? groups[0] : "");

  const form = useForm<FormValues>({
    resolver: zodResolver(buildFormSchema(protocol, isEdit)) as any,
    defaultValues: {
      name: initial?.name ?? "",
      description: initial?.description ?? "",
      group_id: defaultGroup,
      enabled: initial?.enabled ?? true,
      config: {
        ...defaultConfig(protocol),
        ...(initial?.config ?? {}),
      },
      credentials: blankCredentials(protocol),
    },
  });

  const {
    register,
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = form;

  const configErrors = (errors.config ?? {}) as Record<
    string,
    { message?: string } | undefined
  >;
  const credErrors = (errors.credentials ?? {}) as Record<
    string,
    { message?: string } | undefined
  >;

  const onSubmit = (data: FormValues) => {
    const config = cleanRecord(data.config);
    const credentials = cleanRecord(data.credentials);

    if (isEdit && initial) {
      const input: ConnectionUpdateInput = {
        name: data.name,
        description: data.description ?? "",
        group_id: data.group_id,
        enabled: data.enabled,
        config,
      };
      if (Object.keys(credentials).length > 0) {
        input.credentials = credentials;
      }
      updateMutation.mutate(
        { id: initial.id, input },
        {
          onSuccess: () => {
            toast.success("Connection updated");
            onDone();
          },
          onError: (err) => toast.error(`Update failed: ${err.message}`),
        },
      );
      return;
    }

    const input = {
      protocol,
      name: data.name,
      description: data.description ?? "",
      group_id: data.group_id,
      enabled: data.enabled,
      config,
      credentials,
    } as unknown as ConnectionCreateInput;

    createMutation.mutate(input, {
      onSuccess: () => {
        toast.success("Connection created");
        onDone();
      },
      onError: (err) => toast.error(`Create failed: ${err.message}`),
    });
  };

  const renderField = (
    field: FieldDef,
    section: "config" | "credentials",
    fieldErrors: Record<string, { message?: string } | undefined>,
  ) => {
    const path = `${section}.${field.name}` as const;
    const id = `conn-${section}-${field.name}`;
    const error = fieldErrors[field.name]?.message;

    return (
      <div key={path} className="space-y-1.5">
        <Label htmlFor={id}>
          {field.label}
          {field.optional && (
            <span className="ml-1 text-xs text-muted-foreground">
              (optional)
            </span>
          )}
        </Label>
        {field.type === "switch" ? (
          <div>
            <Controller
              control={control}
              name={path as never}
              render={({ field: f }) => (
                <Switch
                  id={id}
                  checked={!!f.value}
                  onCheckedChange={f.onChange}
                />
              )}
            />
          </div>
        ) : field.type === "textarea" ? (
          <Textarea
            id={id}
            rows={4}
            placeholder={field.placeholder}
            className="font-mono text-xs"
            {...register(path as never)}
          />
        ) : (
          <Input
            id={id}
            type={
              field.type === "password"
                ? "password"
                : field.type === "number"
                  ? "number"
                  : field.type === "url"
                    ? "url"
                    : "text"
            }
            placeholder={field.placeholder}
            autoComplete={field.type === "password" ? "new-password" : undefined}
            {...register(
              path as never,
              field.type === "number" ? { valueAsNumber: true } : {},
            )}
          />
        )}
        {field.help && (
          <p className="text-xs text-muted-foreground">{field.help}</p>
        )}
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  };

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="conn-name">Name</Label>
        <Input
          id="conn-name"
          placeholder="Production SFTP drop"
          {...register("name")}
        />
        {errors.name && (
          <p className="text-xs text-destructive" role="alert">
            {errors.name.message}
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="conn-description">Description</Label>
        <Textarea
          id="conn-description"
          rows={2}
          placeholder="Optional notes"
          {...register("description")}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="conn-group">Group</Label>
        {groups.length > 0 ? (
          <Controller
            control={control}
            name="group_id"
            render={({ field: f }) => (
              <Select value={f.value} onValueChange={f.onChange}>
                <SelectTrigger id="conn-group" aria-label="Group">
                  <SelectValue placeholder="Select a group" />
                </SelectTrigger>
                <SelectContent>
                  {(initial && !groups.includes(initial.group_id)
                    ? [initial.group_id, ...groups]
                    : groups
                  ).map((g) => (
                    <SelectItem key={g} value={g}>
                      {g}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
        ) : (
          <Input
            id="conn-group"
            placeholder="group id"
            {...register("group_id")}
          />
        )}
        {errors.group_id && (
          <p className="text-xs text-destructive" role="alert">
            {errors.group_id.message}
          </p>
        )}
      </div>

      <fieldset className="space-y-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Endpoint</legend>
        {CONFIG_FIELDS[protocol].map((f) =>
          renderField(f, "config", configErrors),
        )}
      </fieldset>

      <fieldset className="space-y-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Credentials</legend>
        {isEdit && (
          <p className="text-xs text-muted-foreground">
            {initial?.credentials_set
              ? "Credentials are set. Leave blank to keep them, or enter new values to replace."
              : "No credentials stored yet. Enter values to add them."}
          </p>
        )}
        {CRED_FIELDS[protocol].map((f) =>
          renderField(f, "credentials", credErrors),
        )}
      </fieldset>

      <div className="flex items-center justify-between rounded-md border p-3">
        <div className="space-y-0.5">
          <Label htmlFor="conn-enabled" className="text-sm font-medium">
            Enabled
          </Label>
          <p className="text-xs text-muted-foreground">
            Disabled connections are skipped by the pipeline.
          </p>
        </div>
        <Controller
          control={control}
          name="enabled"
          render={({ field: f }) => (
            <Switch
              id="conn-enabled"
              checked={f.value}
              onCheckedChange={f.onChange}
            />
          )}
        />
      </div>

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving…" : isEdit ? "Save" : "Create"}
        </Button>
      </DialogFooter>
    </form>
  );
}
