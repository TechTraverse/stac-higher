---
name: add-stac-endpoint
description: Add a new STAC API client function and TanStack Query hook. Use whenever the user asks to call, fetch from, or support a new STAC API endpoint or operation — collections, items, search, conformance, queryables, etc. Covers the client-layer pattern (stacFetch, types, query keys, invalidation).
---

# Add STAC Endpoint

## 1. Pick the domain module

- `app/src/lib/stac-api/collections.ts` — collection operations
- `app/src/lib/stac-api/items.ts` — item operations
- `app/src/lib/stac-api/search.ts` — search and discovery

## 2. Add the API function

All functions use `stacFetch<T>()` from `@/lib/stac-api/client`:
- Accept `endpointUrl?: string` as the last parameter
- `encodeURIComponent()` all path parameters
- Return a type from `@/lib/stac-api/types.ts`

If the endpoint returns a new shape, add the TypeScript interface to
`packages/shared/src/lib/stac-api/types.ts` (the app's `types.ts` is a re-export
proxy). Use `StacApiError` for typed error handling.

## 3. Add the TanStack Query hook

In the corresponding `app/src/lib/query/` file:
- Use `stacKeys` from `app/src/lib/query/keys.ts` for cache keys (add a new key
  to the factory if needed — never inline key arrays)
- Query keys must include the catalog URL so catalog switching invalidates
- Mutations `invalidateQueries` for affected keys on success
- Queries take `enabled` guards for required parameters

## 4. Verify

`npm run verify` from the repo root must pass.
