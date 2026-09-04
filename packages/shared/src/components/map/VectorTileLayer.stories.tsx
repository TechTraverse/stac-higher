import type { Meta, StoryObj } from "@storybook/react-vite";
import { StacMap } from "./StacMap";
import { VectorTileLayer } from "./VectorTileLayer";

/**
 * The overlay the /map page draws for a tipg vector collection. Storybook
 * has no tipg, so these stories use MapLibre's public demo tiles, whose
 * TileJSON carries `countries` (polygons), `geolines` (lines) and
 * `centroids` (points) — one of each geometry type the layer handles.
 */
const meta: Meta<typeof VectorTileLayer> = {
  component: VectorTileLayer,
  title: "Map/VectorTileLayer",
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => (
      <div className="h-[400px] w-full">
        <StacMap>
          <Story />
        </StacMap>
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof VectorTileLayer>;

const DEMO_TILEJSON = "https://demotiles.maplibre.org/tiles/tiles.json";

export const Polygons: Story = {
  args: { id: "demo", url: DEMO_TILEJSON, sourceLayer: "countries" },
};

export const Points: Story = {
  args: { id: "demo", url: DEMO_TILEJSON, sourceLayer: "centroids" },
};

export const Blended: Story = {
  args: { id: "demo", url: DEMO_TILEJSON, sourceLayer: "countries", opacity: 0.7 },
};
