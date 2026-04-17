import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { BboxInput } from "./BboxInput";

const meta: Meta<typeof BboxInput> = {
  component: BboxInput,
  title: "Shared/BboxInput",
  args: {
    onChange: fn(),
  },
  argTypes: {
    value: { table: { disable: true } },
  },
};

export default meta;
type Story = StoryObj<typeof BboxInput>;

export const Empty: Story = {
  args: {
    value: [0, 0, 0, 0],
  },
};

export const WorldBounds: Story = {
  args: {
    value: [-180, -90, 180, 90],
  },
};

export const USBounds: Story = {
  args: {
    value: [-125, 24, -66, 50],
  },
};

export const EuropeBounds: Story = {
  args: {
    value: [-10, 35, 40, 72],
  },
};
