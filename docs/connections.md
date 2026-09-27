# Connections (Phase 2)

Group-owned endpoints (SFTP/FTP/FTPS/SSH/S3 today; `stac-api` reserved) that
the pipeline ingests from and delivers to. This documents the **app side**:
tables, credential encryption, and the `/api/connections` surface. The
pipeline side (protocol adapters, egress policy, drain job) lives in
`services/pipeline`; the app↔pipeline bridge is ADR
[0004](decisions/0004-app-pipeline-bridge.md).

## Data model

Migration 004 (`app/src/lib/db/migrate.ts`) creates two tables in
`stac_higher` — shapes are a cross-runtime contract with the Python
pipeline, which codes against them and never creates them (ADR 0001):

- **`connections`** — one row per endpoint: `protocol`
  (`ssh|sftp|ftp|ftps|s3|stac-api|registry`, migration 030 widened the CHECK),
  per-protocol `config` jsonb, encrypted
  `credentials` bytea, TOFU `host_key` + `host_key_pinned_at` (SSH family),
  `group_id`, health (`status` `unverified|ok|error`, `last_checked_at`,
  `last_error`). `updated_at` is maintained app-side (user edits only) — the
  pipeline's health sweep updates status columns without touching it.
- **`connection_checks`** — the test-connection bridge: the app inserts
  `pending` rows and polls; the pipeline drains them (~10 s) and writes
  `result` `{ok, message, latency_ms?}`.

### Per-protocol `config`

| Protocol | Shape |
|---|---|
| `s3` | `{bucket, region?, endpoint?, force_path_style?, anonymous (default false)}` — `anonymous`: public bucket, unsigned requests, credentials optional; e.g. NOAA NODD `noaa-goes19` (G-1) |
| `ssh` / `sftp` | `{host, port (default 22), root_path (default "/")}` |
| `ftp` | `{host, port (default 21), root_path (default "/")}` |
| `ftps` | ftp + `{implicit (default false)}` |
| `registry` | `{host}`: a bare registry hostname with an optional port (`docker.io`, `ghcr.io`, `123456789012.dkr.ecr.us-gov-west-1.amazonaws.com`). Image pull credentials for user-supplied process images (C-1, ADR 0021). A registry connection cannot carry a data flow (400). |
| `stac-api` | reserved — create/update reject it ("reserved for a future release") |

### Per-protocol `credentials` (write-only)

| Protocol | Shape |
|---|---|
| `s3` | `{access_key_id, secret_access_key, session_token?}` |
| `ssh` / `sftp` | `{username, password?, private_key?, passphrase?}` — at least one of password/private_key |
| `ftp` / `ftps` | `{username, password}` |
| `registry` | `{username, password}` (a PAT, an ECR token, a robot account) |

For s3 with `anonymous: true` the credentials are optional and, if present,
ignored by the adapter. Worked example (no `credentials` key at all):

```json
{ "protocol": "s3", "name": "NOAA NODD GOES-19", "group_id": "…",
  "config": { "bucket": "noaa-goes19", "region": "us-east-1", "anonymous": true } }
```

The compose stack also creates `stac-higher-deliveries`, an empty bucket for
delivery tests; a delivery connection to it uses endpoint `http://minio:9000`
(the pipeline's view), `force_path_style: true`, and the MinIO root
credentials.

## Credential encryption (ROADMAP §5.2)

Credentials are **write-only**: validated on create/update, sealed into an
envelope, and never returned — API responses expose only
`credentials_set: true|false`. The app never decrypts; only the pipeline
does, at job execution time.

Envelope (bytea): `0x01` version byte ‖ 12-byte random nonce ‖ AES-256-GCM
ciphertext+tag of the UTF-8 credential JSON. Implementation:
`app/src/lib/connections/crypto.ts`, behind an `EncryptionProvider`
interface (local master key now; KMS provider in Phase 8).

| Var | Purpose |
|---|---|
| `CREDENTIALS_MASTER_KEY` | base64-encoded 32 bytes. Required for any credential write — missing/malformed keys fail the request loudly (no fallback key). The pipeline service must be configured with the same value. |

Generate a dev key:

```
node -e "console.log(require('node:crypto').randomBytes(32).toString('base64'))"
# or: openssl rand -base64 32
```

and set it in `.env.local` (app) and the pipeline's environment.

## API

All routes under `/api/connections` require authentication. Mutations are
role-gated (operator|admin) by the middleware guard, which also writes the
audit rows; group ownership (ROADMAP §7) is enforced in the routes — a
connection outside your groups is a **404** (existence is group-scoped).

| Route | Method | Access | Notes |
|---|---|---|---|
| `/api/connections` | GET | member+ | Own groups' rows; admin: all. `{connections: [...]}` |
| `/api/connections` | POST | operator+ | `group_id` must be one of the caller's groups (admin: any). 201 → the connection. |
| `/api/connections/[id]` | GET | member+ (owning group) | |
| `/api/connections/[id]` | PUT | operator+ (owning group) | Protocol immutable. `credentials` replaces the envelope wholesale (never merged). Config/credential changes reset `status` to `unverified`; an SSH-family host/port change clears the host-key pin. |
| `/api/connections/[id]` | DELETE | operator+ (owning group) | Soft-delete (ADR 0009): sets `deleted_at`, scrubs credentials + host-key pin, soft-deletes its associations, removes reference-backed items from pgstac; history rows (checks, ledger, delivery log) are retained. Returns the counted impact. |
| `/api/connections/[id]/impact` | GET | operator+ (owning group) | Pre-flight counted impact for the warn-and-proceed delete dialog (associations, ledger rows, reference-backed items). |
| `/api/connections/[id]/test` | POST | operator+ (owning group) | Inserts a pending `connection_checks` row, 202 → `{check}`. Audited as `test`. |
| `/api/connections/[id]/checks/[checkId]` | GET | member+ (owning group) | Poll: `{check}` with `status` pending→running→done\|failed and `result`. |
| `/api/connections/[id]/host-key/reset` | POST | operator+ (owning group) | §5.2 re-verify: clears the TOFU pin (ssh/sftp only) so the next test re-pins. Audited. |

Connection responses expose host keys as metadata only:
`host_key: {fingerprint: "SHA256:…", pinned_at} | null` — never the raw key.

Authz failures use the standard guard shape:
`{"error": "...", "code": "unauthenticated" | "forbidden"}` (401/403).

## Pipeline side

The Python pipeline (`services/pipeline`) is the only runtime that decrypts
`credentials` and the only one with the protocol adapters + egress policy
(ROADMAP §5.2). It codes against these tables and never creates them (ADR 0001).

- **Envelope decrypt** — `connections/envelope.py` mirrors `crypto.ts`
  byte-for-byte (a cross-runtime known-answer test locks the format). Needs the
  same `CREDENTIALS_MASTER_KEY`.
- **Egress policy** — `connections/egress.py` resolves every target host and
  blocks private/loopback/link-local/metadata addresses (deny-by-default;
  `EGRESS_ALLOW_HOSTS` is the only exception). Enforced before any adapter
  socket opens.
- **Adapters + TOFU** — `connections/adapters/` (`s3`, `sftp`/`ssh`, `ftp`,
  `ftps`; `stac-api` reserved). SSH-family `test()` surfaces the server host
  key; `adapters/tofu.py` decides first-pin / match / hard-fail mismatch.
  `S3Adapter` builds an unsigned boto3 client (`botocore.UNSIGNED`) when
  `anonymous` is set; the egress policy still vets the endpoint host. The s3
  `config` shape is pinned by the `s3-connection-config.json` contract fixture.
- **Registry check** — `connections/registry.py`'s `check_registry` is
  `GET https://{host}/v2/` with Basic auth, following a Bearer challenge to its
  HTTPS token endpoint with the same credentials (`docker.io` is probed at
  `registry-1.docker.io`). Every host goes through `resolve_pinned`, and
  nothing is logged but the host and the status.
- **Bridge jobs** — `pipeline.connection_check_drain` (drains
  `connection_checks`) and `pipeline.connection_health_sweep` (tests enabled
  connections). Both update only health/pin columns, never `updated_at`. Drain
  runs on a 1-minute cron that clears the whole backlog each tick (ADR 0004's
  ~10 s target is approximated; sub-minute needs a NOTIFY-woken drain — see ADR
  0004 "Revisit"). Details: `services/pipeline/README.md`.

## Audit

Every connection mutation, test request, and host-key reset lands one
append-only `audit_log` row (via the middleware guard). Audit `detail`
carries only method/path/outcome — request bodies never reach the audit
path, and `sanitizeDetail` redacts credential-shaped material defensively.
