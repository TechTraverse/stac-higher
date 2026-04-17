import type { Meta, StoryObj } from "@storybook/react-vite";
import { JsonViewer } from "./JsonViewer";

const sampleCollection = {
  type: "Collection",
  id: "sentinel-2-l2a",
  stac_version: "1.0.0",
  description: "Sentinel-2 Level-2A surface reflectance data.",
  license: "proprietary",
  extent: {
    spatial: { bbox: [[-180, -90, 180, 90]] },
    temporal: { interval: [["2015-06-23T00:00:00Z", null]] },
  },
  links: [],
};

const deeplyNested = {
  level1: {
    level2: {
      level3: {
        level4: {
          value: "deeply nested value",
          array: [1, 2, 3, { nested: true }],
        },
      },
    },
  },
  metadata: {
    created: "2023-01-01",
    updated: "2024-06-15",
    tags: ["production", "validated", "public"],
  },
};

const largeArray = {
  features: Array.from({ length: 20 }, (_, i) => ({
    id: `item-${i + 1}`,
    properties: { datetime: `2023-${String(i % 12 + 1).padStart(2, "0")}-01T00:00:00Z` },
  })),
};

const meta: Meta<typeof JsonViewer> = {
  component: JsonViewer,
  title: "Shared/JsonViewer",
  parameters: { layout: "padded" },
};

export default meta;
type Story = StoryObj<typeof JsonViewer>;

export const Collapsed: Story = {
  args: {
    data: sampleCollection,
    title: "Raw JSON",
    defaultOpen: false,
  },
};

export const Expanded: Story = {
  args: {
    data: sampleCollection,
    title: "Collection JSON",
    defaultOpen: true,
  },
};

export const NestedObject: Story = {
  args: {
    data: deeplyNested,
    title: "Nested Structure",
    defaultOpen: true,
  },
};

export const LargeArray: Story = {
  args: {
    data: largeArray,
    title: "Feature Collection (20 items)",
    defaultOpen: true,
  },
};
