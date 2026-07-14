import { useAuthMe } from "@/lib/query/auth";
import { Badge, Button } from "@stac-higher/shared";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { FlaskConical, LogIn, LogOut, UserCircle } from "lucide-react";

function signIn() {
  const returnTo = window.location.pathname + window.location.search;
  window.location.href = `/api/auth/login?returnTo=${encodeURIComponent(returnTo)}`;
}

function signOut() {
  window.location.href = "/api/auth/logout";
}

/**
 * Header auth widget:
 * - anonymous (OIDC configured) → "Sign in" button
 * - authenticated              → user menu with identity details + sign out
 * - dev bypass                 → user menu with a subtle "dev" badge
 */
export function UserMenu() {
  const { data: auth, isLoading, isError } = useAuthMe();

  // Never block or clutter the header on auth problems.
  if (isLoading || isError || !auth) return null;

  if (!auth.authenticated) {
    return (
      <Button variant="outline" size="sm" onClick={signIn}>
        <LogIn className="h-4 w-4 mr-1.5" />
        Sign in
      </Button>
    );
  }

  const { identity, mode } = auth;
  const displayName = identity.name ?? identity.email ?? identity.sub;
  const isBypass = mode === "bypass";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5">
          <UserCircle className="h-4 w-4" />
          <span className="hidden md:inline max-w-32 truncate">
            {displayName}
          </span>
          {isBypass && (
            <Badge
              variant="outline"
              className="text-[10px] px-1 py-0 text-muted-foreground"
            >
              <FlaskConical className="h-2.5 w-2.5 mr-0.5" />
              dev
            </Badge>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>
          <div className="font-medium truncate">{displayName}</div>
          {identity.email && (
            <div className="text-xs text-muted-foreground font-normal truncate">
              {identity.email}
            </div>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <div className="px-2 py-1.5 flex flex-wrap gap-1">
          {identity.roles.map((role) => (
            <Badge key={role} variant="secondary" className="text-xs">
              {role}
            </Badge>
          ))}
          {identity.groups.map((group) => (
            <Badge key={group} variant="outline" className="text-xs">
              {group}
            </Badge>
          ))}
        </div>
        {isBypass ? (
          <>
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5 text-xs text-muted-foreground">
              Dev identity (auth bypass) — no IdP session
            </div>
          </>
        ) : (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={signOut}>
              <LogOut className="h-4 w-4 mr-2" />
              Sign out
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
