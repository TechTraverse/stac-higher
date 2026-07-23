# ADR 0008 — Browser catalog writes go through an app BFF route

- **Status:** accepted (decision); implementation pending (pre-Phase 6)
- **Owners:** app control plane (`app/src/lib/stac-api/`, `app/src/pages/api/`,
  `app/src/middleware.ts`)
- **Related:** ADR 0002 (auth-proxy enforcement scope), ROADMAP §7 (enforcement
  by plane), [ISSUES.md](../ISSUES.md) I-50; `docs/auth.md`

## Context

The 2026-07-22 pre-B-iii architecture review confirmed a structural gap in the
production auth posture: **in auth-enforced mode
(`infra/compose.auth-enforced.yml`), the platform's own UI cannot write to the
built-in catalog.**

- ADR 0002 enforcement requires a valid `stac-higher`-audience bearer token on
  every transaction at the proxy (:8081).
- The browser client (`stacFetch`, `app/src/lib/stac-api/client.ts`) attaches
  no `Authorization` header, and the built-in catalog entry is seeded without
  the proxy flag, so item/collection create/edit/delete goes browser → :8081
  directly and 401s the moment enforcement is on.
- The user's access token is sealed inside the httpOnly AEAD session cookie —
  deliberately unreadable by page JavaScript (`docs/auth.md`), so the browser
  *cannot* present it. `/api/proxy` only forwards a browser-supplied
  `authorization` header the browser cannot produce.
- Phase 1's enforcement integration tests minted tokens via the password-grant
  test client (which ADR 0002 says must never ship), masking the gap.

A second review finding lands in the same seam: catalog-plane mutations — the
platform's primary data plane — never touch `stac_higher.audit_log`, because
the audit write path lives only in the app's API middleware guard, which
browser→:8081 traffic bypasses entirely.

## Decision

**The app becomes a Backend-for-Frontend (BFF) for built-in-catalog writes.**

1. **Browser transactions route through an app server route** (a dedicated
   passthrough under `/api/` — exact path is an implementation choice; a
   dedicated route is preferred over overloading `/api/proxy`, so the
   mutation guard and audit table can match it by prefix). The route forwards
   the method/body to the built-in catalog URL and **injects the caller's
   session access token server-side** as the `Authorization` header. Token
   refresh is already handled by the session middleware; the token never
   reaches page JavaScript.
2. **Scope: writes only, built-in catalog only.** Catalog reads stay on their
   current direct path (public reads per ADR 0002; per-collection
   read-visibility remains the proxy/OPA concern tracked as I-1). External
   catalogs are untouched — browse-only, via `/api/proxy` as today.
3. **External API clients are unaffected.** They keep presenting their own
   bearer tokens directly to the proxy (:8081); the BFF is for browser
   sessions only. Phase 7 push-ingest clients follow ADR 0002's existing
   contract.
4. **Catalog mutations join the audit log.** The BFF route is registered in
   the existing permission-guard route table, so every item/collection
   mutation gets the same RBAC gate + append-only `audit_log` row as the rest
   of the control plane — closing the catalog-plane audit gap in the same
   stroke.
5. **The client routes built-in-catalog transactions to the BFF
   unconditionally** (dev pass-through included), so the dev and enforced
   paths exercise the same code and the seam can't silently regress.

This **amends ROADMAP §7's "enforcement by plane"**: catalog *writes from
browser sessions* are now mediated by the Astro API (which still delegates
token validation to the proxy — the app adds the token, the proxy remains the
enforcement point). The proxy continues to enforce all non-browser traffic.

## Rejected alternatives

- **Expose the access token to the browser** (e.g. a `/api/auth/token`
  endpoint or non-httpOnly storage) so the client can call :8081 itself.
  Reverses the deliberate httpOnly/AEAD session design, expands XSS blast
  radius from "session actions" to "bearer token exfiltration", and still
  leaves catalog mutations un-audited. Rejected.
- **Proxy-side session support** (teach stac-auth-proxy to accept the app's
  session cookie). Couples the proxy to the app's cookie format and
  encryption key, duplicates session logic in a second runtime, and drifts
  from upstream stac-auth-proxy. Rejected.
- **Keep the UI read-only under enforcement.** Contradicts the product: the
  client's CRUD surface (items, collections, extensions) is a core feature,
  not an optional admin add-on.

## Consequences

- The UI works under the production security posture; enforcement mode stops
  being a "backend-only" configuration.
- The plane boundary shifts slightly: for browser users, the control plane is
  now on the catalog write path. The route handler must stay thin (forwarding
  + token injection only) so the catalog plane remains the source of truth
  for STAC semantics and validation.
- The enforcement integration suite (`tests/integration/`) must gain a
  UI-path leg: session login → BFF write → 201 through the enforced proxy,
  replacing reliance on the password-grant client for this case.
- `stacFetch`/mutation call sites need a routing branch for the built-in
  catalog; external-catalog code paths are untouched.
- Until implemented, enforcement mode remains demo-blocking for UI CRUD —
  tracked as **I-50** so it can't be mistaken for done.

## Revisit

- When I-1 (read-visibility) lands: decide whether authenticated *reads*
  should also route through the BFF for group-scoped filtering, or stay at
  the proxy with OPA/CQL2 — the BFF route makes either option cheap.
- Phase 7: external push clients may want the staging/finalize flow's
  presigned-upload brokering under the same route family; keep the route
  naming generic enough to grow.
