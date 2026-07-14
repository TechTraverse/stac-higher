import { CatalogSelector } from "@/components/catalogs/CatalogSelector";
import { UserMenu } from "@/components/layout/UserMenu";
import { ThemeToggle } from "@stac-higher/shared";
import { Layers, Search, Puzzle, Database } from "lucide-react";
import { Button } from "@stac-higher/shared";

const navLinks = [
  { href: "/", label: "Dashboard", icon: null },
  { href: "/catalogs", label: "Catalogs", icon: Database },
  { href: "/collections", label: "Collections", icon: Layers },
  { href: "/search", label: "Search", icon: Search },
  { href: "/extensions", label: "Extensions", icon: Puzzle },
];

export function Header() {
  const pathname = typeof window !== "undefined" ? window.location.pathname : "/";

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-14 items-center px-4 gap-4">
        <a href="/" className="flex items-center gap-2 font-semibold text-lg shrink-0">
          <div className="h-7 w-7 rounded-md bg-primary flex items-center justify-center">
            <Layers className="h-4 w-4 text-primary-foreground" />
          </div>
          <span className="hidden sm:inline">STAC Higher</span>
        </a>

        <nav className="flex items-center gap-1 ml-4">
          {navLinks.map((link) => {
            const isActive =
              link.href === "/"
                ? pathname === "/"
                : pathname.startsWith(link.href);
            return (
              <a key={link.href} href={link.href}>
                <Button
                  variant={isActive ? "secondary" : "ghost"}
                  size="sm"
                  className="text-sm"
                >
                  {link.icon && <link.icon className="h-4 w-4 mr-1.5" />}
                  {link.label}
                </Button>
              </a>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <CatalogSelector />
          <UserMenu />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
