/**
 * The application shell (ADR 0017): QueryProvider + navy sidebar + slim top
 * bar, wrapping every page island. Replaces the old top-header `Header`.
 *
 * NOTE on `<main>`: shadcn's `SidebarInset` renders a `<main>`, and every page
 * island already renders its own. Rather than restructure 20 islands (and risk
 * nested `<main>` elements), the shell provides a plain flex column and the
 * island keeps ownership of its `<main>`.
 */
import { useStore } from "@nanostores/react";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { SidebarNav } from "@/components/layout/SidebarNav";
import { TopBar } from "@/components/layout/TopBar";
import { SidebarProvider } from "@/components/ui/sidebar";
import { $sidebarOpen, toggleSidebar } from "@/stores/uiStore";

interface AppShellProps {
  children: React.ReactNode;
  /** Overrides the route-derived label in the top bar. */
  title?: string;
}

export function AppShell({ children, title }: AppShellProps) {
  // The rail's expanded/collapsed state is cross-island nanostore state, so
  // the sidebar primitive runs controlled rather than on its own cookie.
  const open = useStore($sidebarOpen);

  return (
    <QueryProvider>
      <SidebarProvider
        open={open}
        onOpenChange={(next) => {
          if (next !== $sidebarOpen.get()) toggleSidebar();
        }}
      >
        <SidebarNav />
        <div className="flex min-h-svh min-w-0 flex-1 flex-col">
          <TopBar title={title} />
          <div className="flex min-h-0 flex-1 flex-col">{children}</div>
        </div>
      </SidebarProvider>
    </QueryProvider>
  );
}
