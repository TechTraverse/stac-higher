/**
 * What every catalog-browser island needs from its route: the path's catalog
 * id, plus the `?src=` catalog URL when the link was shared (UI-15). Both come
 * from the Astro shell rather than `window.location`, so the island has them
 * on its first render.
 */
export interface BrowseRouteProps {
  catalogId: string;
  src?: string | null;
}
