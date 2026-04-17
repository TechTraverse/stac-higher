import { atom } from "nanostores";
import { persistentAtom } from "@nanostores/persistent";

export const $theme = persistentAtom<"light" | "dark">("stac-theme", "dark");

export const $sidebarOpen = atom(true);

export function toggleTheme() {
  const next = $theme.get() === "dark" ? "light" : "dark";
  $theme.set(next);
  document.documentElement.className = next === "dark" ? "dark" : "";
}

export function toggleSidebar() {
  $sidebarOpen.set(!$sidebarOpen.get());
}
