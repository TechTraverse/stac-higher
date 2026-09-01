/**
 * The shell's slim top bar (ADR 0017): page context on the left, global
 * search plus the alert bell / theme toggle / user menu on the right.
 *
 * The search is a CLIENT-SIDE filter over data the app already fetches —
 * products, connections and processes — not a new endpoint. Its queries live
 * in a child that only mounts once the user has typed, so an idle top bar
 * costs no requests on every page.
 *
 * The CatalogSelector deliberately does NOT live here: it is catalog-context
 * UI and now renders inside the Catalogs and Search pages only.
 */
import { useMemo, useRef, useState } from "react";
import { useStore } from "@nanostores/react";
import { Layers, Plug, Cpu, Search as SearchIcon, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Input, ThemeToggle } from "@stac-higher/shared";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { AlertBell } from "@/components/layout/AlertBell";
import { UserMenu } from "@/components/layout/UserMenu";
import { $activeCatalog } from "@/stores/catalogStore";
import { useCollections } from "@/lib/query/collections";
import { useConnections } from "@/lib/connections/queries";
import { useProcesses } from "@/lib/processes/queries";
import { cn } from "@/lib/utils";

/** Route → page label, used when a page doesn't name itself. */
const ROUTE_TITLES: Array<[RegExp, string]> = [
  [/^\/$/, "Products"],
  [/^\/collections\/[^/]+\/items\/new$/, "Create item"],
  [/^\/collections\/[^/]+\/items\/[^/]+\/edit$/, "Edit item"],
  [/^\/collections\/[^/]+\/items\/[^/]+$/, "Item"],
  [/^\/collections\/[^/]+\/items$/, "Items"],
  [/^\/collections\/[^/]+\/edit$/, "Edit product"],
  [/^\/collections\/new$/, "Create product"],
  [/^\/collections\/[^/]+$/, "Product"],
  [/^\/collections$/, "Products"],
  [/^\/processes\/[^/]+$/, "Process"],
  [/^\/processes$/, "Processes"],
  [/^\/connections$/, "Connections"],
  [/^\/graph$/, "Pipeline graph"],
  [/^\/monitoring$/, "Monitoring"],
  [/^\/catalogs$/, "Catalogs"],
  [/^\/search$/, "Search"],
  [/^\/extensions\/new$/, "Create extension"],
  [/^\/extensions\/[^/]+\/edit$/, "Edit extension"],
  [/^\/extensions\/[^/]+$/, "Extension"],
  [/^\/extensions$/, "Extensions"],
];

function titleForPath(pathname: string): string {
  for (const [pattern, label] of ROUTE_TITLES) {
    if (pattern.test(pathname)) return label;
  }
  return "STAC Higher";
}

interface Hit {
  href: string;
  label: string;
  detail: string;
  group: string;
  icon: LucideIcon;
}

const MAX_HITS = 8;

function SearchResults({ query, onPick }: { query: string; onPick: () => void }) {
  const catalog = useStore($activeCatalog);
  const { data: collections } = useCollections(catalog?.url ?? "");
  const { data: connections } = useConnections();
  const { data: processes } = useProcesses();

  const hits = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return [];
    const out: Hit[] = [];

    for (const c of collections?.collections ?? []) {
      const label = c.title ?? c.id;
      if (
        label.toLowerCase().includes(needle) ||
        c.id.toLowerCase().includes(needle)
      ) {
        out.push({
          href: `/collections/${encodeURIComponent(c.id)}`,
          label,
          detail: c.id,
          group: "Products",
          icon: Layers,
        });
      }
    }
    for (const c of connections ?? []) {
      if (c.name.toLowerCase().includes(needle)) {
        out.push({
          href: "/connections",
          label: c.name,
          detail: c.protocol,
          group: "Connections",
          icon: Plug,
        });
      }
    }
    for (const p of processes ?? []) {
      if (p.name.toLowerCase().includes(needle)) {
        out.push({
          href: `/processes/${p.id}`,
          label: p.name,
          detail: p.id,
          group: "Processes",
          icon: Cpu,
        });
      }
    }
    return out.slice(0, MAX_HITS);
  }, [query, collections, connections, processes]);

  if (hits.length === 0) {
    return (
      <div className="px-3 py-4 text-sm text-muted-foreground">
        Nothing matches “{query.trim()}”.
      </div>
    );
  }

  return (
    <ul className="max-h-80 overflow-auto py-1">
      {hits.map((hit, i) => {
        const newGroup = i === 0 || hits[i - 1].group !== hit.group;
        return (
          <li key={`${hit.group}-${hit.href}-${hit.label}`}>
            {newGroup && (
              <div className="px-3 pb-1 pt-2 text-[10px] font-bold uppercase tracking-[0.08em] text-muted-foreground">
                {hit.group}
              </div>
            )}
            <a
              href={hit.href}
              onClick={onPick}
              className="flex items-center gap-2.5 px-3 py-2 text-sm hover:bg-accent hover:text-accent-foreground"
            >
              <hit.icon className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate font-medium">
                {hit.label}
              </span>
              <span className="tech shrink-0 truncate text-[11px] text-muted-foreground">
                {hit.detail}
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function GlobalSearch() {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const show = open && query.trim().length > 0;

  return (
    <div
      className="relative hidden md:block"
      onFocus={() => {
        if (blurTimer.current) clearTimeout(blurTimer.current);
        setOpen(true);
      }}
      // A click on a result blurs the input before navigation commits, so the
      // close is deferred rather than immediate.
      onBlur={() => {
        blurTimer.current = setTimeout(() => setOpen(false), 150);
      }}
    >
      <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setQuery("");
            setOpen(false);
          }
        }}
        placeholder="Search products, connections, processes…"
        aria-label="Search products, connections and processes"
        className="h-9 w-56 pl-8 pr-8 lg:w-72 [&::-webkit-search-cancel-button]:appearance-none"
      />
      {query && (
        <button
          type="button"
          onClick={() => setQuery("")}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
      {show && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 overflow-hidden rounded-md border border-border bg-popover shadow-lg">
          <SearchResults query={query} onPick={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}

export function TopBar({ title, className }: { title?: string; className?: string }) {
  const pathname = typeof window !== "undefined" ? window.location.pathname : "/";
  const heading = title ?? titleForPath(pathname);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-4",
        className
      )}
    >
      <SidebarTrigger className="-ml-1" />
      <h2 className="truncate text-base font-bold">{heading}</h2>
      <div className="ml-auto flex items-center gap-2">
        <GlobalSearch />
        <AlertBell />
        <UserMenu />
        <ThemeToggle />
      </div>
    </header>
  );
}
