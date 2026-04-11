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
- Connect to multiple STAC API endpoints
- Switch between endpoints from the header — cached data refreshes automatically
- Test connection button to verify API availability
- Endpoint configuration persisted to localStorage

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
| Icons | [Lucide React](https://lucide.dev/) |
| Toasts | [Sonner](https://sonner.emilkowal.dev/) |
| Unit Tests | [Vitest](https://vitest.dev/) + [Testing Library](https://testing-library.com/) |
| E2E Tests | [Playwright](https://playwright.dev/) |

## Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) >= 22.12.0
- [Docker](https://www.docker.com/) and Docker Compose (for the STAC API backend)

### Setup

```bash
# Clone the repository
git clone <repo-url>
cd stac-higher

# Start the STAC API backend
docker compose up -d

# Install frontend dependencies
cd app
npm install

# Start the dev server
npm run dev
```

The frontend starts at [http://localhost:4321](http://localhost:4321). The STAC API runs at [http://localhost:8082](http://localhost:8082).

On first launch, go to the Endpoints page and add `http://localhost:8082` as your STAC endpoint. From there you can create collections, add items, and search.

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
  docker-compose.yml          # pgstac + stac-fastapi backend
  CLAUDE.md                   # Agent development guide
  app/                        # Astro frontend
    src/
      pages/                  # Astro routing (thin shells mounting React islands)
      layouts/Layout.astro    # HTML shell, theme script, meta tags
      components/
        layout/               # Header, DashboardPage, QueryProvider, ThemeToggle
        collections/          # CollectionList, CollectionDetail, CollectionForm
        items/                # ItemList, ItemDetail, ItemForm, ItemGeometryEditor
        endpoints/            # EndpointManager, EndpointSelector, EndpointForm
        search/               # SearchPage (sidebar + map)
        map/                  # StacMap, FootprintLayer, ExtentLayer, DrawingToolbar
        assets/               # AssetManager
        shared/               # ErrorBoundary, ErrorState, EmptyState, JsonViewer, BboxInput
        ui/                   # shadcn/ui primitives (generated, do not edit by hand)
      lib/
        stac-api/             # Fetch client, TypeScript types, Zod schemas, CRUD functions
        query/                # TanStack Query hooks and key factory
        map/                  # Bbox utilities, MapLibre layer style definitions
      stores/                 # nanostores (endpoints, theme, map state)
    e2e/                      # Playwright E2E tests
    vitest.config.ts
    playwright.config.ts
```

## Architecture

Astro handles routing and serves each page as an SSR shell. Each page mounts a single React island via `client:only="react"` that owns all interactivity for that page. The only cross-island boundary is the Header (separate island) communicating with page content through nanostores.

**State is managed in three tiers:**

1. **Nanostores** for global client state (selected endpoint, theme). Persisted to localStorage. Shared across React islands because they're plain JS module-level atoms.
2. **TanStack Query** for server state (collections, items, search results). Query keys include the endpoint URL, so switching endpoints automatically invalidates stale data.
3. **React Hook Form + Zod** for form state. Schemas define validation rules; converter functions transform between form shape and STAC-compliant JSON.

The API client (`src/lib/stac-api/client.ts`) reads the active endpoint from nanostores and provides a typed `stacFetch<T>()` wrapper that all domain functions and query hooks build on.

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

65 tests across 4 test files covering:
- **Zod schemas** (`schemas.test.ts`): validation of collection and item form data, edge cases for IDs, bbox, provider roles
- **Bbox utilities** (`bbox.test.ts`): coordinate conversion and geometry-to-bbox extraction for all GeoJSON types
- **API client** (`client.test.ts`): URL construction, request headers, error handling for 4xx/5xx, 204 responses

### E2E Tests

```bash
cd app
npm run test:e2e
```

4 Playwright tests covering the endpoint management workflow: empty state, adding/deleting endpoints, automatic activation. Playwright auto-starts the dev server.

## Backend

The `docker-compose.yml` at the repo root starts two services:

- **database**: PostgreSQL with PostGIS and pgstac extensions (port 5433)
- **api**: stac-fastapi-pgstac with the Transaction extension enabled (port 8082)

```bash
# Start
docker compose up -d

# Stop
docker compose down

# Stop and remove data
docker compose down -v
```

The frontend does not require the backend to start — it connects to whatever endpoint URLs you configure in the UI. The docker-compose setup is provided for local development and testing.
