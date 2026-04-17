import type { Meta, StoryObj } from "@storybook/react-vite";
import { ItemCard } from "./ItemCard";
import {
  mockItem,
  mockItemDateRange,
  mockItemNoGeometry,
  mockItemManyAssets,
} from "@shared/components/__fixtures__/stac";

const meta: Meta<typeof ItemCard> = {
  component: ItemCard,
  title: "Items/ItemCard",
  parameters: { layout: "padded" },
  args: {
    collectionId: "sentinel-2-l2a",
  },
  decorators: [
    (Story) => (
      <div className="max-w-xl">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof ItemCard>;

export const Default: Story = {
  args: {
    item: mockItem,
  },
};

export const DateRange: Story = {
  args: {
    item: mockItemDateRange,
    collectionId: "composites",
  },
};

export const NoGeometry: Story = {
  args: {
    item: mockItemNoGeometry,
    collectionId: "tabular",
  },
};

export const MultipleAssets: Story = {
  args: {
    item: mockItemManyAssets,
  },
};

export const InList: Story = {
  render: () => (
    <div className="space-y-2 max-w-xl">
      <ItemCard item={mockItem} collectionId="sentinel-2-l2a" />
      <ItemCard item={mockItemDateRange} collectionId="composites" />
      <ItemCard item={mockItemNoGeometry} collectionId="tabular" />
      <ItemCard item={mockItemManyAssets} collectionId="sentinel-2-l2a" />
    </div>
  ),
};
