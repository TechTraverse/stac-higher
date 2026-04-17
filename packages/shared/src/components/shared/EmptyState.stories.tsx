import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { Database, FolderOpen, Search } from "lucide-react";
import { EmptyState } from "./EmptyState";

const meta: Meta<typeof EmptyState> = {
  component: EmptyState,
  title: "Shared/EmptyState",
  argTypes: {
    icon: { table: { disable: true } },
    action: { table: { disable: true } },
  },
};

export default meta;
type Story = StoryObj<typeof EmptyState>;

export const NoAction: Story = {
  args: {
    icon: Database,
    title: "No collections found",
    description: "There are no collections in this STAC API yet.",
  },
};

export const WithAction: Story = {
  args: {
    icon: FolderOpen,
    title: "No collections found",
    description: "Get started by creating your first collection.",
    action: {
      label: "Create Collection",
      onClick: fn(),
    },
  },
};

export const WithLinkAction: Story = {
  args: {
    icon: Search,
    title: "No results",
    description: "No items matched your search criteria. Try adjusting your filters.",
    action: {
      label: "Browse All Items",
      href: "/items",
    },
  },
};
