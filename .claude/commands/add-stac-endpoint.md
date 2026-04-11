Add a new STAC API endpoint function. The endpoint is: $ARGUMENTS

Follow these steps:

1. Determine which domain module the endpoint belongs to:
   - `src/lib/stac-api/collections.ts` — collection operations
   - `src/lib/stac-api/items.ts` — item operations
   - `src/lib/stac-api/search.ts` — search and discovery

2. Add the function using the existing pattern — all functions use `stacFetch<T>()` from `@/lib/stac-api/client`:
   - Accept `endpointUrl?: string` as last parameter
   - Use `encodeURIComponent()` for path parameters
   - Return the appropriate type from `@/lib/stac-api/types.ts`

3. If the endpoint returns a new type, add the TypeScript interface to `src/lib/stac-api/types.ts`.

4. Create a TanStack Query hook in the corresponding `src/lib/query/` file:
   - Use `stacKeys` from `src/lib/query/keys.ts` for cache keys (add new key if needed)
   - Mutations should `invalidateQueries` for affected keys on success
   - Queries should have `enabled` guards for required parameters

5. Run `cd $CLAUDE_PROJECT_DIR/app && npx astro check` to verify 0 errors.
