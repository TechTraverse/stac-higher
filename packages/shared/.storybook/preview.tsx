import type { Preview, Decorator } from "@storybook/react-vite";
import { useEffect } from "react";
import { TooltipProvider } from "@shared/components/ui/tooltip";
import { $theme } from "@shared/stores/uiStore";
import "./preview.css";
// Same faces, same order as app/src/layouts/Layout.astro.
import "@fontsource-variable/public-sans";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/600.css";

const withThemeAndProviders: Decorator = (Story, context) => {
  const theme = (context.globals.theme as "light" | "dark") || "light";

  useEffect(() => {
    $theme.set(theme);
    document.documentElement.className = theme === "dark" ? "dark" : "";
  }, [theme]);

  return (
    <TooltipProvider>
      <div className="bg-background text-foreground min-h-screen p-6">
        <Story />
      </div>
    </TooltipProvider>
  );
};

const preview: Preview = {
  globalTypes: {
    theme: {
      description: "Toggle light/dark theme",
      toolbar: {
        title: "Theme",
        icon: "moon",
        items: [
          { value: "light", icon: "sun", title: "Light" },
          { value: "dark", icon: "moon", title: "Dark" },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: {
    // Light is the product default (ADR 0017) — Storybook opens the same way.
    theme: "light",
  },
  decorators: [withThemeAndProviders],
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /date$/i,
      },
    },
    layout: "centered",
  },
};

export default preview;
