# STAC Higher

A modern web interface for browsing, searching, and managing SpatioTemporal Asset Catalogs (STAC). Full CRUD for collections, items, and assets with interactive map visualization.

<!-- Add a screenshot here: ![STAC Higher Dashboard](docs/screenshot.png) -->

## Features

### Catalog Management
- **Collections**: Create, view, edit, and delete STAC collections with form validation
- **Items**: Full CRUD with geometry drawing (polygon, rectangle, point), dynamic properties, and asset management
- **Assets**: Add, edit, and delete collection-level and item-level assets
- **Bulk Import**: Paste a JSON array of items and import them with a live progress bar
- **Live JSON Preview**: See the STAC-compliant JSON update in real time as you fill out forms

### Map Visualization
- Interactive maps on collection detail (spatial extent), item detail (geometry footprint), item list (all footprints), and search results
- Geometry drawing tools with polygon, bounding box, and point modes
- Interactive bbox drawing on the collection form for defining spatial extents
- Theme-aware basemaps (CartoDB dark-matter / positron) that switch with the app theme
- Hover-to-highlight: mousing over an item card highlights its footprint on the map

### Search
- Cross-collection search via POST /search
- Filter by collection (multi-select), temporal range, spatial bbox, and CQL2-Text expressions
- Results rendered on a full-page map with a responsive sidebar

### Multi-API Support
- Connect to multiple STAC API catalogs
- Switch between catalogs from the header — cached data refreshes automatically
- Test connection button to verify API availability
- Catalog configuration persisted to localStorage
- **CORS Proxy**: Enable per-catalog server-side proxying to reach APIs that don't send CORS headers. Requests are routed through `/api/proxy` on the Astro server, so the browser never touches the remote origin directly.

### Extension Management
- Browse, create, edit, and delete custom STAC extension definitions stored in a local PostgreSQL database
- **Dynamic forms**: When an extension is attached to a collection or item, its JSON Schema fields render as a live form (via RJSF with shadcn/ui widgets) — no manual property entry needed
- **Import external extensions**: Paste any STAC extension schema URL (or pick from the curated list: EO, SAR, View, Projection, Timestamps) to import it locally
- **Schema hosting**: Every local extension is served as a JSON Schema at `/api/extensions/{id}/schema` — usable as a `stac_extensions` URL
- Extension properties flow through to `item.properties` (items) and `summaries` (collections)

### UX
- Dark and light themes with system preference detection
- Responsive layouts (search page stacks on mobile)
- Token-based pagination for item lists
- Page-specific loading skeletons
- Toast notifications for all mutations
- Keyboard navigation and aria-labels on interactive elements
- React error boundaries for graceful failure handling

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | [Astro 6](https://astro.build/) (SSR) + [React 19](https://react.dev/) islands |
| Maps | [MapLibre GL JS](https://maplibre.org/) via [react-map-gl](https://visgl.github.io/react-map-gl/) |
| Styling | [Tailwind CSS 4](https://tailwindcss.com/) + [shadcn/ui](https://ui.shadcn.com/) (Radix primitives) |
| Server State | [TanStack Query](https://tanstack.com/query) |
| Client State | [nanostores](https://github.com/nanostores/nanostores) (cross-island, persistent) |
| Forms | [React Hook Form](https://react-hook-form.com/) + [Zod](https://zod.dev/) |
| Dynamic Forms | [RJSF](https://rjsf-team.github.io/react-jsonschema-form/) (`@rjsf/core` + `@rjsf/validator-ajv8`) |
| Database | [PostgreSQL](https://www.postgresql.org/) via `pg` (for extension storage) |
| Icons | [Lucide React](https://lucide.dev/) |
| Toasts | [Sonner](https://sonner.emilkowal.dev/) |
| Unit Tests | [Vitest](https://vitest.dev/) + [Testing Library](https://testing-library.com/) |
| E2E Tests | [Playwright](https://playwright.dev/) |

## Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) >= 22.12.0
- [Docker](https://www.docker.com/) and Docker Compose (for the STAC API backend and the extension database)

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://username:password@localhost:5433/postgis` | PostgreSQL connection string for extension storage |

The database is provided by the included docker-compose setup. For production, set `DATABASE_URL` to point at your PostgreSQL instance. The app runs schema migrations automatically on the first request.

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd stac-higher

# Start the STAC API backend + PostgreSQL database
docker compose up -d

# Install frontend dependencies
cd app
npm install

# Start the dev server
npm run dev
```

The frontend starts at [http://localhost:4321](http://localhost:4321). The built-in catalog is served through stac-auth-proxy at [http://localhost:8081](http://localhost:8081) (stac-fastapi directly at [http://localhost:8082](http://localhost:8082)).

On first launch, the Catalogs page already contains the undeletable **Built-in Catalog** entry pointing at `http://localhost:8081` (override with `PUBLIC_BUILTIN_CATALOG_URL`). From there you can create collections, add items, search, and manage extensions — or add further external catalogs.

### Storybook

The shared component library lives in `packages/shared/` and has its own Storybook:

```bash
cd packages/shared
npm run storybook        # http://localhost:6006
npm run build-storybook  # static build to packages/shared/storybook-static/
```

## Scripts

All scripts run from the `app/` directory.

| Script | Command | Description |
|---|---|---|
| Dev server | `npm run dev` | Start Astro dev server at localhost:4321 |
| Build | `npm run build` | Production build to `dist/` |
| Preview | `npm run preview` | Preview the production build |
| Type check | `npx astro check` | Run TypeScript type checking |
| Unit tests | `npm test` | Run Vitest unit tests |
| Unit tests (watch) | `npm run test:watch` | Run Vitest in watch mode |
| E2E tests | `npm run test:e2e` | Run Playwright browser tests |

## Project Structure

```
stac-higher/
  package.json                # npm workspaces root
  package-lock.json           # single lockfile for all workspaces
  docker-compose.yml          # pgstac + stac-fastapi + PostgreSQL backend
  CLAUDE.md                   # Agent development guide
  packages/
    shared/                   # @stac-higher/shared — reusable components, hooks, types,
                              #   stores, RJSF theme, and Storybook
      src/
        components/{shared,layout,map,collections,items,extensions,ui}/
        lib/{map,stac-api,utils}
        stores/{uiStore,mapStore}
        index.ts              # public barrel (consumed by the app)
  app/                        # Astro frontend
    src/
      pages/
        api/
          proxy.ts            # CORS proxy route (X-Proxy-Target header)
          extensions/         # Extension CRUD, schema hosting, import, resolve-schema
        extensions/           # Extension UI pages (list, detail, new, edit)
        catalogs.astro        # Catalog management page
      layouts/Layout.astro    # HTML shell, theme script, meta tags
      components/
        layout/               # Header, DashboardPage, QueryProvider (app-specific)
        collections/          # CollectionList, CollectionDetail, CollectionForm
        items/                # ItemList, ItemDetail, ItemForm, ItemGeometryEditor
        catalogs/             # CatalogManager, CatalogSelector, CatalogForm
        extensions/           # ExtensionList, ExtensionDetail, ExtensionForm,
                              #   ExtensionPicker, ExtensionFields, ImportExtensionDialog
        search/               # SearchPage (sidebar + map)
        assets/               # AssetManager
        ui/                   # App-only shadcn primitives: dialog, dropdown-menu, popover,
                              #   separator, sheet, sonner, table, tabs
      lib/                    # App-local utilities + thin proxies that re-export from
                              #   @stac-higher/shared (utils, map/bbox, map/styles,
                              #   stac-api/types, stores/uiStore, stores/mapStore)
        stac-api/             # Fetch client, Zod schemas, CRUD functions (types proxy shared)
        query/                # TanStack Query hooks and key factory
        db/                   # PostgreSQL connection pool (connection.ts) + migrations (migrate.ts)
        extensions/           # Extension storage (storage.ts), types, Zod schemas,
                              #   API client (api.ts), TanStack Query hooks (queries.ts),
                              #   JSON Schema cache (schema-cache.ts)
      stores/                 # catalogStore (app-only); uiStore/mapStore are proxies
    e2e/                      # Playwright E2E tests
    vitest.config.ts
    playwright.config.ts
```

## Architecture

Astro handles routing and serves each page as an SSR shell. Each page mounts a single React island via `client:only="react"` that owns all interactivity for that page. The only cross-island boundary is the Header (separate island) communicating with page content through nanostores.

**State is managed in three tiers:**

1. **Nanostores** for global client state (selected catalog, theme). Persisted to localStorage. Shared across React islands because they're plain JS module-level atoms.
2. **TanStack Query** for server state (collections, items, search results). Query keys include the catalog URL, so switching catalogs automatically invalidates stale data.
3. **React Hook Form + Zod** for form state. Schemas define validation rules; converter functions transform between form shape and STAC-compliant JSON.

The API client (`src/lib/stac-api/client.ts`) reads the active catalog from nanostores and provides a typed `stacFetch<T>()` wrapper that all domain functions and query hooks build on.

## STAC API Endpoints

The UI communicates with these STAC API endpoints:

| Method | Path | Operation |
|---|---|---|
| GET | `/` | Landing page / API info |
| GET | `/collections` | List collections |
| GET | `/collections/{id}` | Get collection |
| POST | `/collections` | Create collection |
| PUT | `/collections/{id}` | Update collection |
| DELETE | `/collections/{id}` | Delete collection |
| GET | `/collections/{id}/items` | List items (paginated) |
| GET | `/collections/{id}/items/{itemId}` | Get item |
| POST | `/collections/{id}/items` | Create item |
| PUT | `/collections/{id}/items/{itemId}` | Replace item |
| PATCH | `/collections/{id}/items/{itemId}` | Partial update item |
| DELETE | `/collections/{id}/items/{itemId}` | Delete item |
| POST | `/search` | Cross-collection search |

Write operations require the [STAC Transaction Extension](https://github.com/stac-api-extensions/transaction). The included docker-compose backend (`stac-fastapi-pgstac`) has this enabled by default.

## Testing

### Unit Tests

```bash
cd app
npm test
```

143 tests across 8 test files covering:
- **Zod schemas** (`schemas.test.ts`): validation of collection and item form data, edge cases for IDs, bbox, provider roles
- **Bbox utilities** (`bbox.test.ts`): coordinate conversion and geometry-to-bbox extraction for all GeoJSON types
- **API client** (`client.test.ts`): URL construction, request headers, error handling for 4xx/5xx, 204 responses
- **Proxy route** (`proxy.test.ts`): header validation, URL scheme checks, request forwarding
- **Extension schemas** (`extensions-schemas.test.ts`): Zod validation, `formToExtensionSchema()` output, round-trips
- **RJSF theme widgets** (`rjsf-theme.test.tsx`): each widget renders correctly, handles changes, disabled states
- **Extension form round-trips** (`extension-roundtrip.test.ts`): `formToStacItem`/`stacItemToForm` and `formToStacCollection`/`stacCollectionToForm` with extension properties

### E2E Tests

```bash
cd app
npm run test:e2e
```

Playwright tests covering: catalog management workflow, CORS proxy flow, extension CRUD (create/view/edit/delete), and dynamic extension form rendering. Playwright auto-starts the dev server. A running PostgreSQL database (via `docker compose up -d`) is required for extension-related tests.

## Backend

The `docker-compose.yml` at the repo root starts two services:

- **database**: PostgreSQL 15 with PostGIS and pgstac extensions (port 5433). Also used by the Astro app for extension storage in the `stac_higher` schema.
- **api**: stac-fastapi-pgstac with the Transaction extension enabled (port 8082)

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Stop and remove data
docker compose down -v
```

The frontend does not require the backend to start — it connects to whatever catalog URLs you configure in the UI. The docker-compose setup is provided for local development and testing.
