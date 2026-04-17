import type { Meta, StoryObj } from "@storybook/react-vite";
import { useEffect } from "react";
import { $theme } from "@shared/stores/uiStore";
import { ThemeToggle } from "./ThemeToggle";

const meta: Meta<typeof ThemeToggle> = {
  component: ThemeToggle,
  title: "Layout/ThemeToggle",
};

export default meta;
type Story = StoryObj<typeof ThemeToggle>;

export const Dark: Story = {
  decorators: [
    (Story) => {
      useEffect(() => {
        $theme.set("dark");
        document.documentElement.className = "dark";
      }, []);
      return <Story />;
    },
  ],
};

export const Light: Story = {
  decorators: [
    (Story) => {
      useEffect(() => {
        $theme.set("light");
        document.documentElement.className = "";
      }, []);
      return <Story />;
    },
  ],
};
