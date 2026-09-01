/**
 * Zod schemas for connections (ROADMAP Phase 2 + §5 CONNECTIONS).
 *
 * Per-protocol `config` and `credentials` shapes follow the cross-runtime
 * contract — the Python pipeline parses the same JSON, so these shapes must
 * not drift:
 *
 *   config      s3        {bucket, region?, endpoint?, force_path_style?}
 *               ssh/sftp  {host, port (default 22), root_path (default "/")}
 *               ftp       {host, port (default 21), root_path (default "/")}
 *               ftps      ftp + {implicit (default false)}
 *   credentials s3        {access_key_id, secret_access_key, session_token?}
 *               ssh/sftp  {username, password?, private_key?, passphrase?}
 *                         (at least one of password / private_key)
 *               ftp/ftps  {username, password}
 *
 * `stac-api` is RESERVED: it exists in the DB CHECK and in
 * CONNECTION_PROTOCOLS (the adapter model must not foreclose it — ROADMAP
 * §1), but create/update reject it with a clear validation error.
 *
 * Credentials are validated on WRITE only — they are never read back through
 * the API, so there is no response-side credential schema by design.
 */
import { z } from "zod";

/** Everything the DB enum admits, including the reserved future protocol. */
export const CONNECTION_PROTOCOLS = [
  "ssh",
  "sftp",
  "ftp",
  "ftps",
  "s3",
  "stac-api",
] as const;
export type ConnectionProtocol = (typeof CONNECTION_PROTOCOLS)[number];

/** Protocols a connection can actually be created with today. */
export const WRITABLE_PROTOCOLS = ["ssh", "sftp", "ftp", "ftps", "s3"] as const;
export type WritableProtocol = (typeof WRITABLE_PROTOCOLS)[number];

export const STAC_API_RESERVED_MESSAGE =
  "The 'stac-api' protocol is reserved for a future release and cannot be used yet";

/** SSH-family protocols carry TOFU-pinned host keys (ROADMAP §5.2). */
export function isSshFamily(protocol: string): protocol is "ssh" | "sftp" {
  return protocol === "ssh" || protocol === "sftp";
}

// ---------------------------------------------------------------------------
// config shapes (stored as-is in connections.config jsonb)
// ---------------------------------------------------------------------------

export const s3ConfigSchema = z
  .object({
    bucket: z.string().min(1, "bucket is required"),
    region: z.string().min(1).optional(),
    endpoint: z.string().url("endpoint must be a URL").optional(),
    force_path_style: z.boolean().optional(),
  })
  .strict();

const hostSchema = z.string().min(1, "host is required");
const rootPathSchema = z.string().min(1).default("/");

export const sshConfigSchema = z
  .object({
    host: hostSchema,
    port: z.number().int().min(1).max(65535).default(22),
    root_path: rootPathSchema,
  })
  .strict();

export const ftpConfigSchema = z
  .object({
    host: hostSchema,
    port: z.number().int().min(1).max(65535).default(21),
    root_path: rootPathSchema,
  })
  .strict();

export const ftpsConfigSchema = z
  .object({
    host: hostSchema,
    port: z.number().int().min(1).max(65535).default(21),
    root_path: rootPathSchema,
    implicit: z.boolean().default(false),
  })
  .strict();

// ---------------------------------------------------------------------------
// credential shapes (validated on write, then sealed into the envelope —
// never stored or returned as plaintext)
// ---------------------------------------------------------------------------

export const s3CredentialsSchema = z
  .object({
    access_key_id: z.string().min(1, "access_key_id is required"),
    secret_access_key: z.string().min(1, "secret_access_key is required"),
    session_token: z.string().min(1).optional(),
  })
  .strict();

/** The object arm, kept separate from the refinement so CREDENTIAL_KEYS can
 * read `.shape` — a refined schema has no shape to read. */
const sshCredentialsObject = z
  .object({
    username: z.string().min(1, "username is required"),
    password: z.string().min(1).optional(),
    private_key: z.string().min(1).optional(),
    passphrase: z.string().min(1).optional(),
  })
  .strict();

export const sshCredentialsSchema = sshCredentialsObject.refine(
  (c) => c.password !== undefined || c.private_key !== undefined,
  {
    message: "At least one of password or private_key is required",
    path: ["password"],
  },
);

export const ftpCredentialsSchema = z
  .object({
    username: z.string().min(1, "username is required"),
    password: z.string().min(1, "password is required"),
  })
  .strict();

const CONFIG_SCHEMAS = {
  s3: s3ConfigSchema,
  ssh: sshConfigSchema,
  sftp: sshConfigSchema,
  ftp: ftpConfigSchema,
  ftps: ftpsConfigSchema,
} as const;

const CREDENTIALS_SCHEMAS = {
  s3: s3CredentialsSchema,
  ssh: sshCredentialsSchema,
  sftp: sshCredentialsSchema,
  ftp: ftpCredentialsSchema,
  ftps: ftpCredentialsSchema,
} as const;

/**
 * The credential-envelope keys a `secret_ref` may name, per protocol
 * (Phase 9 §5.6 — see `docs/processes.md`). DERIVED from the schemas above so
 * a new credential field shows up in the process env editor's key picker
 * automatically instead of drifting into a ref nothing can resolve.
 *
 * This map is also the honest statement of the limit: a `secret_ref` reaches
 * a CONNECTION's credentials and nothing else, so these ~8 names are the
 * entire secret namespace a process can address today.
 */
export const CREDENTIAL_KEYS: Record<WritableProtocol, readonly string[]> = {
  s3: Object.keys(s3CredentialsSchema.shape),
  ssh: Object.keys(sshCredentialsObject.shape),
  sftp: Object.keys(sshCredentialsObject.shape),
  ftp: Object.keys(ftpCredentialsSchema.shape),
  ftps: Object.keys(ftpCredentialsSchema.shape),
};

/** Keys for any protocol string, including the reserved `stac-api` (none). */
export function credentialKeysFor(protocol: string): readonly string[] {
  return CREDENTIAL_KEYS[protocol as WritableProtocol] ?? [];
}

export function configSchemaFor(protocol: WritableProtocol) {
  return CONFIG_SCHEMAS[protocol];
}

export function credentialsSchemaFor(protocol: WritableProtocol) {
  return CREDENTIALS_SCHEMAS[protocol];
}

// ---------------------------------------------------------------------------
// create / update payloads
// ---------------------------------------------------------------------------

const baseCreateFields = {
  name: z.string().min(1, "name is required").max(200),
  description: z.string().max(2000).default(""),
  group_id: z.string().min(1, "group_id is required"),
  enabled: z.boolean().default(true),
};

/**
 * POST /api/connections body — discriminated on protocol so config and
 * credentials are validated against the right per-protocol shape.
 */
export const connectionCreateSchema = z.discriminatedUnion("protocol", [
  z.object({
    protocol: z.literal("s3"),
    ...baseCreateFields,
    config: s3ConfigSchema,
    credentials: s3CredentialsSchema,
  }),
  z.object({
    protocol: z.literal("ssh"),
    ...baseCreateFields,
    config: sshConfigSchema,
    credentials: sshCredentialsSchema,
  }),
  z.object({
    protocol: z.literal("sftp"),
    ...baseCreateFields,
    config: sshConfigSchema,
    credentials: sshCredentialsSchema,
  }),
  z.object({
    protocol: z.literal("ftp"),
    ...baseCreateFields,
    config: ftpConfigSchema,
    credentials: ftpCredentialsSchema,
  }),
  z.object({
    protocol: z.literal("ftps"),
    ...baseCreateFields,
    config: ftpsConfigSchema,
    // ftps shares the plain ftp credential shape.
    credentials: ftpCredentialsSchema,
  }),
]);

export type ConnectionCreateInput = z.infer<typeof connectionCreateSchema>;

export type ParsedCreate =
  | { success: true; data: ConnectionCreateInput }
  | { success: false; error: z.ZodError };

/** Build a ZodError with one custom issue via a guaranteed-failing parse. */
function customZodError(
  message: string,
  path: (string | number)[],
): z.ZodError {
  const failing = z.unknown().superRefine((_value, ctx) => {
    ctx.addIssue({ code: "custom", message, path });
  });
  const result = failing.safeParse(null);
  // The refinement above always adds an issue, so success is always false.
  return (result as { success: false; error: z.ZodError }).error;
}

/**
 * Parse a create payload, rejecting the reserved 'stac-api' protocol with a
 * dedicated message BEFORE the union runs (the union would otherwise report
 * an opaque "invalid discriminator" error).
 */
export function parseConnectionCreate(data: unknown): ParsedCreate {
  if (
    typeof data === "object" &&
    data !== null &&
    (data as Record<string, unknown>).protocol === "stac-api"
  ) {
    return {
      success: false,
      error: customZodError(STAC_API_RESERVED_MESSAGE, ["protocol"]),
    };
  }
  return connectionCreateSchema.safeParse(data);
}

/**
 * PUT /api/connections/[id] body. Protocol is immutable — the update shape
 * is validated against the EXISTING row's protocol (config/credential shapes
 * depend on it), so this is a two-step parse: `connectionUpdateSchema` for
 * the outer shape, then `parseConnectionUpdate` with the row's protocol for
 * config/credentials. `credentials`, when present, REPLACES the stored
 * envelope wholesale — partial credential merges do not exist.
 */
export const connectionUpdateSchema = z
  .object({
    name: baseCreateFields.name.optional(),
    description: z.string().max(2000).optional(),
    group_id: baseCreateFields.group_id.optional(),
    enabled: z.boolean().optional(),
    protocol: z.enum(CONNECTION_PROTOCOLS).optional(),
    config: z.unknown().optional(),
    credentials: z.unknown().optional(),
  })
  .strict();

export interface ConnectionUpdateInput {
  name?: string;
  description?: string;
  group_id?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  credentials?: Record<string, unknown>;
}

export type ParsedUpdate =
  | { success: true; data: ConnectionUpdateInput }
  | { success: false; error: z.ZodError };

function updateFailure(
  message: string,
  path: (string | number)[],
): ParsedUpdate {
  return { success: false, error: customZodError(message, path) };
}

export function parseConnectionUpdate(
  data: unknown,
  existingProtocol: ConnectionProtocol,
): ParsedUpdate {
  const outer = connectionUpdateSchema.safeParse(data);
  if (!outer.success) return { success: false, error: outer.error };
  const body = outer.data;

  if (body.protocol !== undefined && body.protocol !== existingProtocol) {
    return updateFailure(
      "protocol is immutable; create a new connection instead",
      ["protocol"],
    );
  }
  // Defensive: rows can only carry 'stac-api' if inserted out-of-band, but
  // reject updates against them explicitly rather than crashing on a missing
  // per-protocol schema.
  if (existingProtocol === "stac-api") {
    return updateFailure(STAC_API_RESERVED_MESSAGE, ["protocol"]);
  }

  const result: ConnectionUpdateInput = {
    name: body.name,
    description: body.description,
    group_id: body.group_id,
    enabled: body.enabled,
  };

  if (body.config !== undefined) {
    const parsed = configSchemaFor(existingProtocol).safeParse(body.config);
    if (!parsed.success) return { success: false, error: parsed.error };
    result.config = parsed.data;
  }
  if (body.credentials !== undefined) {
    const parsed = credentialsSchemaFor(existingProtocol).safeParse(
      body.credentials,
    );
    if (!parsed.success) return { success: false, error: parsed.error };
    result.credentials = parsed.data;
  }
  return { success: true, data: result };
}
