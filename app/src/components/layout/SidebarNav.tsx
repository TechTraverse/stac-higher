/**
 * The platform's primary navigation rail (ADR 0017).
 *
 * Navy in BOTH themes — the dark rail is part of the identity, not a
 * dark-mode artifact, so it reads off the `--color-sidebar-*` tokens rather
 * than the page surface tokens.
 *
 * Ordering encodes the product-centric workflow: the five surfaces a data
 * manager lives in sit under "Operate"; the original STAC-client surfaces
 * (catalog aggregation, search, extension building) are de-emphasized into a
 * collapsed "More" group — relocated, never removed. There are deliberately
 * NO create links here (the brief's settled decision); creation happens from
 * the surface that owns the thing being created.
 */
import { useState } from "react";
import { useStore } from "@nanostores/react";
import {
  Activity,
  ChevronDown,
  Cpu,
  Database,
  Layers,
  Plug,
  Puzzle,
  Search,
  Share2,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import { $activeCatalog } from "@/stores/catalogStore";
import { useLandingPage } from "@/lib/query/search";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Extra path prefixes that should light this item up. */
  alsoMatches?: string[];
}

const OPERATE: NavItem[] = [
  { href: "/", label: "Products", icon: Layers, alsoMatches: ["/collections"] },
  { href: "/processes", label: "Processes", icon: Cpu },
  { href: "/connections", label: "Connections", icon: Plug },
  { href: "/graph", label: "Pipeline graph", icon: Share2 },
  { href: "/monitoring", label: "Monitoring", icon: Activity },
];

const MORE: NavItem[] = [
  { href: "/catalogs", label: "Catalogs", icon: Database },
  { href: "/search", label: "Search", icon: Search },
  { href: "/extensions", label: "Extensions", icon: Puzzle },
];

function matches(item: NavItem, pathname: string): boolean {
  const prefixes = [item.href, ...(item.alsoMatches ?? [])];
  return prefixes.some((p) =>
    p === "/" ? pathname === "/" : pathname === p || pathname.startsWith(`${p}/`)
  );
}

function NavList({ items, pathname }: { items: NavItem[]; pathname: string }) {
  return (
    <SidebarMenu>
      {items.map((item) => {
        const active = matches(item, pathname);
        return (
          <SidebarMenuItem key={item.href}>
            <SidebarMenuButton
              asChild
              isActive={active}
              tooltip={item.label}
              className={cn(
                "relative gap-2.5 font-semibold text-sidebar-foreground/85",
                "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
                "data-[active=true]:bg-sidebar-accent data-[active=true]:text-sidebar-accent-foreground",
                // The mockup's 3px accent bar on the active row.
                active &&
                  "before:absolute before:left-0 before:top-1/2 before:h-3.5 before:w-[3px] before:-translate-y-1/2 before:rounded-full before:bg-sidebar-primary"
              )}
            >
              <a href={item.href}>
                <item.icon className="h-4 w-4 shrink-0" />
                <span>{item.label}</span>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        );
      })}
    </SidebarMenu>
  );
}

/**
 * Stack reachability, read off the landing-page query the dashboard already
 * runs — no new endpoint, and the cached result is shared.
 */
function StackStatus() {
  const catalog = useStore($activeCatalog);
  const { data, isLoading, isError } = useLandingPage(catalog?.url ?? "");

  const state = !catalog
    ? ("none" as const)
    : isLoading
      ? ("checking" as const)
      : isError || !data
        ? ("down" as const)
        : ("up" as const);

  const label = {
    none: "No catalog configured",
    checking: "Checking STAC API…",
    down: "STAC API unreachable",
    up: "STAC API reachable",
  }[state];

  const dot = {
    none: "bg-sidebar-muted",
    checking: "bg-sidebar-muted animate-pulse",
    down: "bg-danger",
    up: "bg-success",
  }[state];

  return (
    <div className="flex flex-col gap-1.5 px-2 py-1 group-data-[collapsible=icon]:hidden">
      {catalog && (
        <div className="tech truncate text-[11px] text-sidebar-muted">
          {catalog.name}
        </div>
      )}
      <div className="flex items-center gap-2 text-xs text-sidebar-foreground">
        <span className={cn("h-2 w-2 shrink-0 rounded-full", dot)} />
        <span className="truncate">{label}</span>
      </div>
    </div>
  );
}

export function SidebarNav() {
  const pathname = typeof window !== "undefined" ? window.location.pathname : "/";
  const moreIsActive = MORE.some((item) => matches(item, pathname));
  const [moreOpen, setMoreOpen] = useState(moreIsActive);

  return (
    <Sidebar
      collapsible="icon"
      className="border-sidebar-border [&_[data-slot=sidebar-container]]:bg-sidebar"
    >
      <SidebarHeader className="border-b border-sidebar-border pb-3">
        <a
          href="/"
          className="flex items-center gap-2.5 px-2 py-1 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:justify-center"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-sidebar-primary">
            <Layers className="h-4 w-4 text-sidebar-primary-foreground" />
          </span>
          <span className="min-w-0 group-data-[collapsible=icon]:hidden">
            <span className="block truncate text-[15px] font-bold leading-tight text-white">
              STAC Higher
            </span>
            <span className="block truncate text-[10px] font-semibold uppercase tracking-[0.09em] text-sidebar-muted">
              Geospatial Data Platform
            </span>
          </span>
        </a>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="text-[10px] font-bold uppercase tracking-[0.1em] text-sidebar-muted">
            Operate
          </SidebarGroupLabel>
          <SidebarGroupContent>
            <NavList items={OPERATE} pathname={pathname} />
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <button
            type="button"
            onClick={() => setMoreOpen((v) => !v)}
            aria-expanded={moreOpen}
            className="flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-[10px] font-bold uppercase tracking-[0.1em] text-sidebar-muted transition-colors hover:text-sidebar-foreground outline-none focus-visible:ring-1 focus-visible:ring-sidebar-ring group-data-[collapsible=icon]:hidden"
          >
            More
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform",
                moreOpen && "rotate-180"
              )}
            />
          </button>
          {/* In the icon rail there is no room for a disclosure, so the group
              is always rendered — the icons stay reachable. */}
          <SidebarGroupContent
            className={cn(
              !moreOpen && "hidden group-data-[collapsible=icon]:block"
            )}
          >
            <NavList items={MORE} pathname={pathname} />
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border">
        <StackStatus />
      </SidebarFooter>
    </Sidebar>
  );
}
