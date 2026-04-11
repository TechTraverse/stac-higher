Create a new React component for the STAC Higher UI. The component name is: $ARGUMENTS

Before writing code, follow these steps:

1. Determine where the component belongs based on its purpose:
   - `src/components/collections/` — collection-related UI
   - `src/components/items/` — item-related UI
   - `src/components/map/` — map layers, controls, drawing tools
   - `src/components/search/` — search panel and results
   - `src/components/shared/` — reusable across pages
   - `src/components/endpoints/` — endpoint management
   - `src/components/assets/` — asset management
   - `src/components/layout/` — header, providers, page wrappers

2. Read 2-3 existing components in the target directory to match conventions:
   - Import from `@/` path alias
   - Use shadcn/ui primitives (`@/components/ui/`) for all UI elements
   - Use `lucide-react` for icons
   - Use `useStore()` from `@nanostores/react` for global state
   - Use TanStack Query hooks from `@/lib/query/` for server data

3. Create the component file.

4. Run `cd $CLAUDE_PROJECT_DIR/app && npx astro check` to verify 0 errors.
