import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { ErrorState } from "./ErrorState";

const meta: Meta<typeof ErrorState> = {
  component: ErrorState,
  title: "Shared/ErrorState",
};

export default meta;
type Story = StoryObj<typeof ErrorState>;

export const Default: Story = {
  args: {
    message: "Failed to load collections from the STAC API.",
  },
};

export const WithRetry: Story = {
  args: {
    message: "Network error while fetching items. Check your connection and try again.",
    onRetry: fn(),
  },
};

export const CustomTitle: Story = {
  args: {
    title: "Collection not found",
    message: "The collection you requested does not exist or has been deleted.",
    onRetry: fn(),
  },
};
