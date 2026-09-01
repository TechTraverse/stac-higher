import { atom } from "nanostores";
import { persistentAtom } from "@nanostores/persistent";

// Light is the default (ADR 0017). Keep in lockstep with the pre-hydration
// script in app/src/layouts/Layout.astro, which reads the same key.
export const $theme = persistentAtom<"light" | "dark">("stac-theme", "light");

export const $sidebarOpen = atom(true);

export function toggleTheme() {
  const next = $theme.get() === "dark" ? "light" : "dark";
  $theme.set(next);
  document.documentElement.className = next === "dark" ? "dark" : "";
}

export function toggleSidebar() {
  $sidebarOpen.set(!$sidebarOpen.get());
}
