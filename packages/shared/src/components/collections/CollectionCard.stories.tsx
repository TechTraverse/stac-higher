import type { Meta, StoryObj } from "@storybook/react-vite";
import { CollectionCard } from "./CollectionCard";
import {
  mockCollection,
  mockCollectionMinimal,
  mockCollectionNoTitle,
  mockCollectionManyKeywords,
} from "@shared/components/__fixtures__/stac";

const meta: Meta<typeof CollectionCard> = {
  component: CollectionCard,
  title: "Collections/CollectionCard",
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof CollectionCard>;

export const Default: Story = {
  args: {
    collection: mockCollection,
  },
};

export const MinimalFields: Story = {
  args: {
    collection: mockCollectionMinimal,
  },
};

export const NoTitle: Story = {
  args: {
    collection: mockCollectionNoTitle,
  },
};

export const ManyKeywords: Story = {
  args: {
    collection: mockCollectionManyKeywords,
  },
};

export const InGrid: Story = {
  render: () => (
    <div className="grid grid-cols-2 gap-4 max-w-2xl">
      <CollectionCard collection={mockCollection} />
      <CollectionCard collection={mockCollectionMinimal} />
      <CollectionCard collection={mockCollectionNoTitle} />
      <CollectionCard collection={mockCollectionManyKeywords} />
    </div>
  ),
};
