import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { DrawingToolbar } from "./DrawingToolbar";

const meta: Meta<typeof DrawingToolbar> = {
  component: DrawingToolbar,
  title: "Map/DrawingToolbar",
  args: {
    onModeChange: fn(),
  },
  decorators: [
    (Story) => (
      <div className="relative h-40 w-16 bg-muted rounded-lg">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof DrawingToolbar>;

export const NoActiveMode: Story = {
  args: {
    activeMode: null,
  },
};

export const PolygonMode: Story = {
  args: {
    activeMode: "polygon",
  },
};

export const BboxMode: Story = {
  args: {
    activeMode: "bbox",
  },
};

export const PointMode: Story = {
  args: {
    activeMode: "point",
  },
};
