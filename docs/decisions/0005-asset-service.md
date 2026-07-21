# ADR 0005 — Asset service: filename-keyed redirect route + direct-to-canonical UI uploads

- **Status:** accepted (Phase 3)
- **Owners:** asset service (app) + platform storage (pipeline)
- **Related:** ROADMAP §3 ("Asset access"), §5.3, §6.2/§6.3, Phase 3; ADR 0002 (read-visibility)

## Context

Phase 3 puts item asset bytes behind the app instead of exposing storage
directly (ROADMAP §3): an item's asset `href` points at
`/api/assets/{collection}/{item}/{asset}`, which authorizes the caller and
302-redirects to a short-lived presigned URL. Two shapes had to be pinned down.

1. **What is the `{asset}` path segment?** The bucket layout (§5.3) keys
   canonical objects by *filename* (`assets/{collection}/{item_id}/{filename}`),
   but a STAC item's `assets` map is keyed by an arbitrary *asset key* (`data`,
   `thumbnail`). If the route segment were the asset key, resolving the object
   would require fetching the item JSON from the catalog on every download.

2. **Where do manual (item-form) uploads land?** §6.3 says the UI reuses "the
   same presigned-upload path as flow B" — which uploads to `staging/` and
   relies on a finalize worker (§6.2) to move bytes to canonical. That finalize
   worker is Phase 7. Without it, a staged upload would never resolve through
   the (canonical-only) asset route, so Phase 3's done-when could not be met.

## Decision

**1. The `{asset}` segment is the stored object's filename.** The asset route
maps `(collection, item, filename)` straight to the canonical key with no
catalog round-trip. Resolution is stateless. Uploads set the item's asset
`href` to `/api/assets/{collection}/{item}/{filename}` and default the STAC
asset *key* to the filename (the user may rename it). Every untrusted segment
is validated before it reaches a key (`assertSafeSegment` rejects
path-traversal; `sanitizeFilename` rewrites the filename to a safe basename) —
`app/src/lib/storage/keys.ts`.

**2. Manual UI uploads write directly to canonical storage.** `POST /api/uploads`
presigns PUTs straight into `assets/{collection}/{item}/{filename}`. Rationale:
the app owns the item id and is a trusted, RBAC-gated writer (operator+), so the
staging→finalize dance (whose job is to *quarantine and validate untrusted
external bytes*, §6.2) buys nothing here. The staging layout still exists
(`stagingKey` in the same module, plus the pipeline's TTL sweep) as the seam the
Phase 7 external push path builds on.

**3. Redirect target is abstracted (`resolveAssetTarget`).** The route depends
only on `{ url }`; `resolveAssetTarget` presigns the canonical object today.
Phase 4's `storage_mode: reference` branches *inside* this function — look up
the association's mode and return the source href for referenced assets —
without touching the route. `app/src/lib/storage/resolve.ts`.

**4. Asset-read authorization = authentication (for now).** The asset route
requires an authenticated identity; unauthenticated callers get 403.
Per-collection / group-scoped read authorization is the same capability the
Phase 1 read-visibility work defers (ADR 0002 / ISSUES I-1) — until that lands,
authentication is the honest boundary. In dev-bypass mode the static operator
satisfies it, so local flows work unchanged.

## Consequences

- The asset route is stateless and cheap — no catalog fetch per download — at
  the cost of tying the URL's last segment to the filename rather than the STAC
  asset key. Two assets on one item must have distinct filenames.
- Manually-uploaded bytes are **not** validated/checksummed server-side in
  Phase 3 (no finalize step); the trusted-writer assumption carries that. The
  external push path (Phase 7) adds validation on its staging→canonical move.
- Presigning is offline, so the app never streams asset bytes and holds no
  long-lived storage connection — but the signing endpoint must be
  **browser-reachable** (ISSUES I-15).
- `reference` mode, retention/GC of canonical assets, and per-collection read
  authz all have a named place to land (this function / ADR 0002) rather than
  reshaping the route later.
- **Slice C change (DB dependency):** Since reference mode was implemented,
  `resolveAssetTarget` queries the `ingest_files (item_id)` index on every asset
  GET — both copy and reference modes now depend on the app database (previously
  copy-mode was pure offline presign). This is deliberate: on a DB error the route
  fails fast (500) rather than falling back to canonical presign, because a
  fallback would mis-serve reference-mode items (bytes not in canonical storage) —
  a 500 is safer than a confident 404. The per-request cost is negligible
  (single indexed lookup); a negative cache or item-level mode marker are
  possible future optimizations if volume warrants it (cross-reference ISSUES I-34).

## Revisit

When Phase 4 introduces `storage_mode` and Phase 7 introduces the finalize
worker, revisit whether manual uploads should also route through staging for a
uniform validation path — the direct-to-canonical shortcut is a Phase-3
simplification, not a permanent invariant.

**Update — Phase 4 Slice C (`storage_mode: reference`), 2026-07-20:** shipped
as the third branch of `resolveAssetTarget` predicted above, but narrower than
"return the source href for referenced assets" implied: it only resolves
**durably-reachable** sources. The pipeline persists a stable,
credential-free source URL (`S3Adapter.public_object_url`) in
`ingest_files.source_href` at FETCH; the route (`lookupReferenceHref` in
`app/src/lib/storage/reference.ts`) 302s straight to it with **no presigning
and no decryption** — the app's decryption boundary (`crypto.ts`, ADR-adjacent
invariant: only the pipeline ever holds decrypted connection credentials) is
unmodified by this branch. A source that requires credentials to read (a
private bucket, SFTP/FTP) has **no** reference path yet — it must use `copy`
mode. Private-source reference is deferred to a pipeline resolver endpoint
that would mint a fresh presigned URL per read on the app's behalf (tracked as
ISSUES I-32); a reference-mode checksum (I-33) and the source-endpoint
browser-reachability split, the same class of gap as I-15 (I-34), are also
deferred. Live end-to-end verification of this branch (Task 10) is pending as
of this update.
