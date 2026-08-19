/**
 * Header alert bell (M2-D, spec §7): unread FIRING-alert count for the
 * current user, linking to /monitoring. "Unread" is the M2-C per-user
 * watermark — opening /monitoring advances it, which zeroes this badge.
 * Renders nothing while unauthenticated or when the count endpoint errors
 * (the header must never break on a monitoring problem).
 */
import { Bell } from "lucide-react";
import { Button } from "@stac-higher/shared";
import { useUnreadAlerts } from "@/lib/monitoring/queries";

export function AlertBell() {
  const { data: unread, isError, isLoading } = useUnreadAlerts();

  if (isError || isLoading) return null;
  const count = unread ?? 0;

  return (
    <a href="/monitoring" data-testid="alert-bell" aria-label="Alerts">
      <Button variant="ghost" size="sm" className="relative px-2">
        <Bell className="h-4 w-4" />
        {count > 0 && (
          <span
            data-testid="alert-bell-count"
            className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-none text-white"
          >
            {count > 99 ? "99+" : count}
          </span>
        )}
      </Button>
    </a>
  );
}
