import type { Meta, StoryObj } from "@storybook/react-vite";
import { LoadingState } from "./LoadingState";

const meta: Meta<typeof LoadingState> = {
  component: LoadingState,
  title: "Shared/LoadingState",
};

export default meta;
type Story = StoryObj<typeof LoadingState>;

export const Default: Story = {};

export const CustomMessage: Story = {
  args: {
    message: "Fetching collections...",
  },
};
