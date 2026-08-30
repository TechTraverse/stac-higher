# stac-higher-proxy-policy

The stac-auth-proxy **items filter factory** that makes
`collection_settings.externally_writable` real policy for direct (non-browser)
catalog writes — ADR 0015, Phase 7 spec §5. It runs **inside** the proxy
process, only in the auth-enforced compose overlay; pass-through mode never
loads it.

Per request the factory returns CQL2:

- **Reads → `true`** (unrestricted), including `POST /search` — the write
  branch keys on transaction-shaped item paths, not method alone.
- **Item writes** → `true` when the request carries the correct `X-BFF-Auth`
  shared secret (app-mediated writes, RBAC-gated + audited app-side);
  otherwise the token needs an `operator`/`admin` role AND the write is
  constrained to `collection IN (<externally-writable, non-archived set>)`
  read from `stac_higher.collection_settings` (read-only, ~15 s TTL cache).
  Empty set → constant `false`, never `IN ()`.
- **`bulk_items` → `false`** without the BFF header (bulk push unsupported in
  Phase 7; the bulk body is never parsed).

## Wiring

- Image: `infra/proxy-policy/Dockerfile` — `FROM` the pinned upstream proxy
  (v1.2.0, which ships no Postgres driver) + `pip install` this package.
- Config: `infra/compose.auth-enforced.yml` sets `ITEMS_FILTER_CLS`,
  overrides `ITEMS_FILTER_PATH` to also route `bulk_items`, and passes the
  env below.

Env (kwargs via `ITEMS_FILTER_KWARGS` take precedence):

| Var | Required | Meaning |
|---|---|---|
| `CATALOG_BFF_SHARED_SECRET` | **yes** — startup fails without it | BFF exemption secret (constant-time compare; never logged) |
| `POLICY_DATABASE_URL` | **yes** | App database DSN; a read-only role suffices |
| `POLICY_ROLES_CLAIM` | no (`realm_access.roles`) | Dotted path to the roles claim |
| `POLICY_WRITE_ROLES` | no (`operator,admin`) | Roles allowed to write directly |
| `POLICY_CACHE_TTL_SECONDS` | no (`15`) | Externally-writable set cache TTL |

## Development

```sh
uv sync --extra dev
uv run pytest          # unit tests — no DB, no Docker, loader injected
uv run ruff check .
```

The tests validate every emitted expression with the same `cql2` engine the
proxy embeds, and mirror the exact context shape
`Cql2BuildFilterMiddleware.__call__` passes (`{"req": {...}, "payload": ...}`).
The live path (derived image + overlay + Keycloak tokens) is exercised by
`tests/integration/proxy-policy.test.mjs` against the enforced stack (P7-Z).
