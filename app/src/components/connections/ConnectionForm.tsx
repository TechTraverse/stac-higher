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
  S3_CREDENTIALS_REQUIRED_MESSAGE,
  STAC_API_RESERVED_MESSAGE,
  configSchemaFor,
  credentialsSchemaFor,
  s3CredentialsSchema,
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

// Per-protocol field descriptors. ssh/sftp share one config + credential shape
// (mirroring schemas.ts, where the Zod schemas are the same object), and
// ftp/ftps share credentials — defined once and aliased so an edit can't drift
// between "identical" copies.
const hostConfigFields = (placeholder: string): FieldDef[] => [
  { name: "host", label: "Host", type: "text", placeholder },
  { name: "port", label: "Port", type: "number" },
  { name: "root_path", label: "Root path", type: "text", placeholder: "/" },
];
const sshConfigFields = hostConfigFields("sftp.example.com");
// FTP servers that don't chroot logins resolve paths from the server root, so
// root_path must be the absolute server-side directory (B-iii live finding —
// e.g. the delfer test server needs /ftp/demo, not /).
const ftpConfigFields = hostConfigFields("ftp.example.com").map((f) =>
  f.name === "root_path"
    ? {
        ...f,
        help:
          "Servers that don't restrict (chroot) logins to their home " +
          "directory need the absolute server-side path here, e.g. /ftp/demo.",
      }
    : f,
);

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
    {
      name: "anonymous",
      label: "Anonymous (public bucket)",
      type: "switch",
      optional: true,
      help: "Public buckets such as NOAA NODD need no keys; requests are sent unsigned.",
    },
  ],
  ssh: sshConfigFields,
  sftp: sshConfigFields,
  ftp: ftpConfigFields,
  ftps: [
    ...ftpConfigFields,
    { name: "implicit", label: "Implicit TLS", type: "switch", optional: true },
  ],
  registry: [
    {
      name: "host",
      label: "Registry host",
      type: "text",
      placeholder: "ghcr.io",
      help: "Bare hostname, no scheme: docker.io, ghcr.io, or an ECR host such as 123456789012.dkr.ecr.us-gov-west-1.amazonaws.com.",
    },
  ],
};

const sshCredFields: FieldDef[] = [
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
];
const ftpCredFields: FieldDef[] = [
  { name: "username", label: "Username", type: "text" },
  { name: "password", label: "Password", type: "password" },
];

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
  ssh: sshCredFields,
  sftp: sshCredFields,
  ftp: ftpCredFields,
  ftps: ftpCredFields,
  registry: [
    { name: "username", label: "Username", type: "text" },
    { name: "password", label: "Password or token", type: "password" },
  ],
};

function defaultConfig(protocol: WritableProtocol): Record<string, unknown> {
  switch (protocol) {
    case "s3":
      return {
        bucket: "",
        region: "",
        endpoint: "",
        force_path_style: false,
        anonymous: false,
      };
    case "ssh":
    case "sftp":
      return { host: "", port: 22, root_path: "/" };
    case "ftp":
      return { host: "", port: 21, root_path: "/" };
    case "ftps":
      return { host: "", port: 21, root_path: "/", implicit: false };
    case "registry":
      return { host: "" };
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

/** Order-insensitive equality for two cleaned config records (flat, primitive
 *  values per the per-protocol schemas). Used to skip resending an unchanged
 *  config on edit. */
function sameConfig(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
): boolean {
  const ak = Object.keys(a).sort();
  const bk = Object.keys(b).sort();
  if (ak.length !== bk.length) return false;
  return ak.every((k, i) => bk[i] === k && a[k] === b[bk[i]]);
}

interface FormValues {
  name: string;
  description: string;
  group_id: string;
  enabled: boolean;
  config: Record<string, unknown>;
  credentials: Record<string, unknown>;
}

/** An s3 connection to a public bucket (G-1): the pipeline signs nothing, so
 *  the credential fields are hidden and never validated or sent. */
function isAnonymousS3(
  protocol: WritableProtocol,
  config: Record<string, unknown> | undefined,
): boolean {
  return protocol === "s3" && config?.anonymous === true;
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
    if (protocol !== "s3") {
      return z.object({
        ...base,
        credentials: z.preprocess(cleanRecord, credentialsSchemaFor(protocol)),
      });
    }
    // s3: whether credentials are required depends on config.anonymous, so
    // validate them in a refinement that can see the config.
    return z
      .object({
        ...base,
        credentials: z.record(z.string(), z.unknown()),
      })
      .superRefine((data, ctx) => {
        if (isAnonymousS3(protocol, data.config as Record<string, unknown>)) {
          return;
        }
        const creds = cleanRecord(data.credentials);
        const parsed = s3CredentialsSchema.safeParse(creds);
        if (parsed.success) return;
        if (Object.keys(creds).length === 0) {
          ctx.addIssue({
            code: "custom",
            path: ["credentials", "access_key_id"],
            message: S3_CREDENTIALS_REQUIRED_MESSAGE,
          });
          return;
        }
        for (const issue of parsed.error.issues) {
          ctx.addIssue({ ...issue, path: ["credentials", ...issue.path] });
        }
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
      if (isAnonymousS3(protocol, data.config as Record<string, unknown>)) {
        return;
      }
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

/** Card labels for the type picker. Values stay the raw protocol strings the
 * API and the pipeline share — only the display text is friendlier. */
const PROTOCOL_ORDER: readonly WritableProtocol[] = [
  "s3",
  "sftp",
  "ssh",
  "ftp",
  "ftps",
  "registry",
];

const PROTOCOL_LABEL: Record<WritableProtocol, string> = {
  s3: "Amazon S3",
  sftp: "SFTP",
  ssh: "SSH",
  ftp: "FTP",
  ftps: "FTPS",
  registry: "Container registry",
};

const PROTOCOL_HINT: Record<WritableProtocol, string> = {
  s3: "Bucket + prefix",
  sftp: "File transfer over SSH",
  ssh: "Remote host, key auth",
  ftp: "Legacy transfer",
  ftps: "FTP over TLS",
  registry: "Image pull credentials",
};

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

        {/* Direction note, not a direction CONTROL.
            The mockup's create screen leads with an Ingest source /
            Distribution destination toggle, but a connection row has no
            direction column — direction lives on the association
            (`collection_connections.direction`), and the same endpoint is
            routinely used both ways. A toggle here would either need an API
            change (out of scope) or silently discard the choice, so the form
            says where direction is actually decided instead. */}
        <p className="rounded-md border border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          A connection is just an endpoint. Whether it acts as an{" "}
          <strong className="font-semibold text-foreground">ingest source</strong>{" "}
          or a{" "}
          <strong className="font-semibold text-foreground">
            distribution destination
          </strong>{" "}
          is chosen when you wire it to a product, on that product&rsquo;s Data
          flow tab — and one endpoint can be both.
        </p>

        <div className="space-y-2">
          <span id="conn-protocol-label" className="text-sm font-medium">
            Type
          </span>
          <div
            role="radiogroup"
            aria-labelledby="conn-protocol-label"
            className="grid grid-cols-2 gap-2 sm:grid-cols-3"
          >
            {PROTOCOL_ORDER.map((p) => {
              const selected = protocol === p;
              return (
                <button
                  key={p}
                  type="button"
                  role="radio"
                  aria-label={p}
                  aria-checked={selected}
                  disabled={isEdit}
                  onClick={() => setProtocol(p)}
                  className={`rounded-md border p-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                    selected
                      ? "border-primary bg-accent"
                      : "border-border hover:border-primary/50"
                  }`}
                >
                  <span
                    className={`block text-[13px] font-bold ${selected ? "text-accent-foreground" : ""}`}
                  >
                    {PROTOCOL_LABEL[p]}
                  </span>
                  <span className="block text-[11px] text-muted-foreground">
                    {PROTOCOL_HINT[p]}
                  </span>
                </button>
              );
            })}
          </div>
          {isEdit && (
            <p className="text-xs text-muted-foreground">
              Type is immutable — create a new connection to change it.
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
    watch,
    formState: { errors },
  } = form;

  const anonymousS3 = isAnonymousS3(protocol, watch("config"));

  const isSaving = createMutation.isPending || updateMutation.isPending;

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
    const anonymous = isAnonymousS3(protocol, config);

    if (isEdit && initial) {
      const input: ConnectionUpdateInput = {
        name: data.name,
        description: data.description ?? "",
        group_id: data.group_id,
        enabled: data.enabled,
      };
      // Only send config when it actually changed: the server resets a
      // verified connection's status to 'unverified' (and clears an SSH
      // host-key pin on host/port changes) whenever config is present, so an
      // unchanged edit must omit it to avoid a spurious status reset.
      if (!sameConfig(config, cleanRecord(initial.config ?? {}))) {
        input.config = config;
      }
      if (!anonymous && Object.keys(credentials).length > 0) {
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
      ...(anonymous ? {} : { credentials }),
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
            // switch/textarea are handled above; the rest (text/number/
            // password/url) are all valid input[type] values.
            type={field.type}
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

      {!anonymousS3 && (
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
      )}

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
        <Button type="submit" disabled={isSaving}>
          {isSaving ? "Saving…" : isEdit ? "Save" : "Create"}
        </Button>
      </DialogFooter>
    </form>
  );
}
