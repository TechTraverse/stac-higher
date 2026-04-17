import { useStore } from "@nanostores/react";
import { $theme, toggleTheme } from "@shared/stores/uiStore";
import { Button } from "@shared/components/ui/button";
import { Moon, Sun } from "lucide-react";

export function ThemeToggle() {
  const theme = useStore($theme);

  return (
    <Button variant="ghost" size="icon" onClick={toggleTheme}>
      {theme === "dark" ? (
        <Sun className="h-4 w-4" />
      ) : (
        <Moon className="h-4 w-4" />
      )}
      <span className="sr-only">Toggle theme</span>
    </Button>
  );
}
