# TODO — STAC Higher

Tasks for agent loop iteration. Pick the top unchecked item, implement it, run `npx astro check` to verify 0 errors, then check it off.

## High Priority — Map Integration

- [x] Add `CollectionExtentMap` component to collection detail overview tab — render the collection's `extent.spatial.bbox` on a `StacMap` using `ExtentLayer`. Auto-fit bounds. File: `app/src/components/collections/CollectionDetail.tsx` (add map to overview tab content).
- [x] Add `ItemFootprintMap` component to item detail geometry tab — render item geometry on a `StacMap` using a `Source`/`Layer`. Auto-fit to item bbox. File: `app/src/components/items/ItemDetail.tsx` (replace JSON-only geometry tab with map + JSON).
- [x] Add map with footprints to item list page — split the items list into a two-column layout with `StacMap` + `FootprintLayer` on one side and the item cards on the other. File: `app/src/components/items/ItemList.tsx`.
- [x] Wire `StacMap` basemap style to `$theme` store — read `useStore($theme)` and pass the correct CartoDB basemap URL (dark-matter for dark, positron for light). File: `app/src/components/map/StacMap.tsx`.

## High Priority — UX Polish

- [x] Add pagination to item list — parse `rel=next` link from STAC response `links` array to get the pagination token, add Previous/Next buttons. Files: `app/src/components/items/ItemList.tsx`, `app/src/lib/query/items.ts`.
- [x] Improve loading states — replace generic `LoadingState` skeleton in `CollectionDetail` and `ItemDetail` with page-specific skeleton layouts that match the actual content structure (tabs, cards, metadata rows).
- [x] Make search page responsive — on viewports below `lg`, stack the search sidebar above the map instead of side-by-side. File: `app/src/components/search/SearchPage.tsx`.
- [x] Add drawing mode hints to `DrawingToolbar` — show tooltip text on hover for each tool explaining the interaction (e.g., "Click vertices, double-click to finish"). File: `app/src/components/map/DrawingToolbar.tsx`.

## Medium Priority — Features

- [x] Add interactive bbox editing to collection form extent section — embed a small `StacMap` with bbox draw capability next to the `BboxInput` fields. Sync drawn bbox back to form values. File: `app/src/components/collections/CollectionForm.tsx`.
- [x] Add CQL2 text filter field to search panel — add a textarea for raw CQL2-Text filter expressions passed as `filter` / `filter-lang` in the search body. File: `app/src/components/search/SearchPage.tsx`.
- [x] Add bulk item import — add a dialog on the items list page that accepts a pasted JSON array of STAC items or a file upload, then POSTs them via `createItem` in sequence with progress. Files: `app/src/components/items/ItemList.tsx` (add button + dialog).
- [x] Add collection-level asset management — create a standalone `AssetManager` component with add/edit/delete for collection assets, reachable from the collection detail page. Files: new `app/src/components/assets/AssetManager.tsx`.

## Medium Priority — Testing

- [x] Set up Vitest — install `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`. Create `vitest.config.ts` with jsdom environment and path aliases. Add `"test": "vitest"` script to package.json.
- [x] Write unit tests for `app/src/lib/stac-api/schemas.ts` — test that valid STAC collections/items pass validation, invalid ones fail with correct messages. Edge cases: null datetime with start/end_datetime, empty bbox, special characters in ID.
- [x] Write unit tests for `app/src/lib/map/bbox.ts` — test `bboxToPolygon`, `bboxToLngLatBounds`, `geometryToBbox` with various geometry types (Point, Polygon, MultiPolygon, GeometryCollection).
- [x] Write unit tests for `app/src/lib/stac-api/client.ts` — mock `fetch`, test URL construction, error handling for 4xx/5xx, 204 responses, JSON parsing failures. Test that `StacApiError` is thrown correctly.
- [x] Set up Playwright — install `@playwright/test`. Create `playwright.config.ts`. Add npm script `"test:e2e": "playwright test"`. Create first E2E test: start dev server, navigate to endpoints page, add an endpoint, verify it appears.

## Lower Priority — Polish & Accessibility

- [x] Add aria-labels to all icon-only buttons (edit, delete, theme toggle, settings) across Header, CollectionDetail, ItemDetail, EndpointManager.
- [x] Add keyboard navigation support — make collection and item cards focusable with `tabIndex={0}` and navigate on Enter/Space keypress.
- [x] Add React error boundaries — wrap each page's React island content in an `ErrorBoundary` component that catches render errors and shows `ErrorState` instead of a blank screen. Create `app/src/components/shared/ErrorBoundary.tsx`.
- [x] Add a custom favicon — create an SVG favicon with a simple globe/layers icon. File: `app/public/favicon.svg`.
- [x] Add Open Graph meta tags to `Layout.astro` — include `og:title`, `og:description`, `og:type` for better link previews.

---

## Phase 1 — CORS Proxy

- [ ] 1.1 Add `proxy?: boolean` field to `StacEndpoint` interface in `app/src/stores/endpointStore.ts`. Optional for backward compatibility with existing localStorage data.
- [ ] 1.2 Create server-side proxy API route at `app/src/pages/api/proxy.ts`. Export `ALL` handler. Read target URL from `X-Proxy-Target` header, validate URL is HTTP(S) and starts with `X-Proxy-Endpoint` base. Forward method/headers/body. Return upstream response. 400 if missing header, 403 if invalid URL.
- [ ] 1.3 Update `stacFetch()` in `app/src/lib/stac-api/client.ts` to route through proxy when active endpoint has `proxy: true`. Rewrite fetch to `/api/proxy` with `X-Proxy-Target`/`X-Proxy-Endpoint` headers. Add `getEndpointForUrl()` helper.
- [ ] 1.4 Add proxy toggle to endpoint form UI. Modify `app/src/components/endpoints/EndpointForm.tsx` (add Switch below URL field: "Proxy through server"). Modify `app/src/components/endpoints/EndpointManager.tsx` (show "Proxied" badge on cards, update `testConnection` to use proxy when enabled).
- [ ] 1.5 Write unit tests for proxy route at `app/src/__tests__/proxy.test.ts`. Test: missing header → 400, non-http scheme → 403, GET forwarding, POST with body, response status/headers forwarded.
- [ ] 1.6 Write E2E test for proxy flow at `app/e2e/proxy.spec.ts`. Add endpoint with proxy enabled, verify badge, test connection through proxy.

## Phase 2 — Database Layer + Extension Infrastructure

- [ ] 2.1 Install `pg` and `@types/pg`. Create database connection module at `app/src/lib/db/connection.ts` — singleton Pool configured from `DATABASE_URL` env var (default: `postgresql://username:password@localhost:5433/postgis`). Export `query()` helper.
- [ ] 2.2 Create migration module at `app/src/lib/db/migrate.ts`. Create `stac_higher` schema and `stac_higher.extensions` table (id UUID PK, name TEXT, prefix TEXT, version TEXT, description TEXT, schema JSONB, source TEXT CHECK local/external, source_url TEXT, created_at/updated_at TIMESTAMPTZ). Export `runMigrations()`. Wire it to run on first API request via middleware.
- [ ] 2.3 Create extension storage module at `app/src/lib/extensions/storage.ts` — CRUD functions using `query()`: `listExtensions`, `getExtension`, `createExtension`, `updateExtension`, `deleteExtension`, `getExtensionBySourceUrl`.
- [ ] 2.4 Define extension TypeScript types at `app/src/lib/extensions/types.ts` — `StacExtension` interface (id, name, prefix, version, description, schema, source, sourceUrl, createdAt, updatedAt), `ExtensionFormData`, `ExtensionPropertyForm`.
- [ ] 2.5 Create extension Zod schemas at `app/src/lib/extensions/schemas.ts` — `extensionFormSchema`, `extensionPropertySchema`. Include `formToExtensionSchema()` and `extensionToForm()` conversion functions.
- [ ] 2.6 Create CRUD API routes: `app/src/pages/api/extensions/index.ts` (GET list, POST create) and `app/src/pages/api/extensions/[id].ts` (GET one, PUT update, DELETE). Validate bodies with Zod.
- [ ] 2.7 Create extension schema hosting route at `app/src/pages/api/extensions/[id]/schema.ts`. Serve JSON Schema with `Content-Type: application/schema+json`. Set `$id` to request URL.
- [ ] 2.8 Create external extension import route at `app/src/pages/api/extensions/import.ts`. POST with `{ url }`, fetch remote JSON Schema, extract metadata, store as `source: "external"`. Deduplicate by `source_url`.
- [ ] 2.9 Create client-side API functions at `app/src/lib/extensions/api.ts` and TanStack Query hooks at `app/src/lib/extensions/queries.ts`. Add `extensionKeys` factory to `app/src/lib/query/keys.ts`.
- [ ] 2.10 Write unit tests: `app/src/__tests__/extensions-schemas.test.ts` (Zod validation, `formToExtensionSchema()` output, round-trip). Storage tests with mocked pg at `app/src/__tests__/extensions-storage.test.ts`.

## Phase 3 — Extension Management UI

- [ ] 3.1 Create extensions list page: `app/src/pages/extensions/index.astro` + `app/src/components/extensions/ExtensionList.tsx`. Grid of extension cards (name, prefix, version, source badge, property count), "Create" and "Import" buttons, empty state. Follow `CollectionList.tsx` pattern.
- [ ] 3.2 Create extension form (create/edit): `app/src/pages/extensions/new.astro`, `app/src/pages/extensions/[id]/edit.astro`, `app/src/components/extensions/ExtensionForm.tsx`. RHF + Zod + useFieldArray for properties. Sections: basic info, properties (name, type, description, required, conditional fields per type). Sticky JSON Schema preview.
- [ ] 3.3 Create extension detail page: `app/src/pages/extensions/[id]/index.astro` + `app/src/components/extensions/ExtensionDetail.tsx`. Show metadata, property table, JSON Schema viewer, copyable schema URL, edit/delete. For external: source URL + refresh button.
- [ ] 3.4 Create import extension dialog at `app/src/components/extensions/ImportExtensionDialog.tsx`. URL input, preview before confirm, curated quick-import list (EO, SAR, View, Projection, Timestamps).
- [ ] 3.5 Add "Extensions" link to navigation in `app/src/components/layout/Header.tsx`.
- [ ] 3.6 Write E2E test at `app/e2e/extensions.spec.ts` — create extension, verify in list, view detail, edit, delete.

## Phase 4 — Fix Extension Data Loss + Extension Selection

- [ ] 4.1 Preserve `stac_extensions` in collection form round-trips. Add `stac_extensions: z.array(z.string()).optional()` to `collectionFormSchema` in `app/src/lib/stac-api/schemas.ts`. Update `stacCollectionToForm()` and `formToStacCollection()` in `app/src/components/collections/CollectionForm.tsx`.
- [ ] 4.2 Preserve `stac_extensions` in item form round-trips. Same changes to `itemFormSchema` and conversion functions in `app/src/components/items/ItemForm.tsx`.
- [ ] 4.3 Create extension picker component at `app/src/components/extensions/ExtensionPicker.tsx`. Multi-select, fetches available extensions via `useExtensions()`, shows name/prefix/version/source badge, operates on schema URL strings. Uses Popover or Command.
- [ ] 4.4 Integrate extension picker into `app/src/components/collections/CollectionForm.tsx`. Add "Extensions" Card section with `ExtensionPicker` controlled by form state. Show selected as dismissible badges.
- [ ] 4.5 Integrate extension picker into `app/src/components/items/ItemForm.tsx`. Same pattern as 4.4.
- [ ] 4.6 Display `stac_extensions` in detail views. Show as badges in `app/src/components/collections/CollectionDetail.tsx` (overview tab) and `app/src/components/items/ItemDetail.tsx` (properties tab header).

## Phase 5 — Dynamic Form Rendering (RJSF + shadcn)

- [ ] 5.1 Install `@rjsf/core`, `@rjsf/utils`, `@rjsf/validator-ajv8`. Create custom shadcn RJSF theme at `app/src/components/extensions/rjsf-theme/` — templates (FieldTemplate, ObjectFieldTemplate, ArrayFieldTemplate) and widgets (TextWidget, SelectWidget, CheckboxWidget, TextareaWidget, NumberWidget) wrapping shadcn/ui primitives. Export assembled theme from `theme.ts`.
- [ ] 5.2 Create `ExtensionFields` container at `app/src/components/extensions/ExtensionFields.tsx`. Given selected extension schema URLs, fetch schemas, render RJSF Form per extension (embedded mode, `tagName="div"`, no submit). Wrap in collapsible Cards. Sync onChange back to parent react-hook-form.
- [ ] 5.3 Integrate dynamic fields into item form. Modify `app/src/components/items/ItemForm.tsx` to render `<ExtensionFields>` after extension picker. Add `extension_properties: z.record(z.string(), z.any()).optional()` to `itemFormSchema`. Update `formToStacItem()` to merge into `item.properties`. Update `stacItemToForm()` to extract by prefix.
- [ ] 5.4 Integrate dynamic fields into collection form. Modify `app/src/components/collections/CollectionForm.tsx` same pattern. Collection extension properties go into `summaries`. Add `extension_properties` to `collectionFormSchema`.
- [ ] 5.5 Create JSON Schema caching at `app/src/lib/extensions/schema-cache.ts` (in-memory Map with TTL). Create resolver route at `app/src/pages/api/extensions/resolve-schema.ts` — POST with `{ url }`, returns cached or freshly fetched schema.
- [ ] 5.6 Write unit tests for RJSF theme widgets at `app/src/__tests__/rjsf-theme.test.tsx`. Test each widget renders correct shadcn component, handles changes, displays errors.
- [ ] 5.7 Write unit tests for form round-trips with extension properties at `app/src/__tests__/extension-roundtrip.test.ts`.
- [ ] 5.8 Write E2E test for dynamic extension forms at `app/e2e/extension-forms.spec.ts` — create extension, create collection/item with it, verify dynamic fields, fill, submit, edit, verify pre-populated.

## Phase 6 — Final Polish

- [ ] 6.1 Update `app/README.md` (document proxy, extensions, DATABASE_URL env var) and `CLAUDE.md` (add extension file locations, API routes, database info).
- [ ] 6.2 Full verification pass: `npx astro check` (0 errors), `npx astro build` (success), `npm test` (all pass), `npm run test:e2e` (all pass).
