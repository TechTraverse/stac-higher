import type { Meta, StoryObj } from "@storybook/react-vite";
import { StacMap } from "./StacMap";
import { RasterTileLayer } from "./RasterTileLayer";

/**
 * The overlay a product item page draws when its collection has OGC serving
 * enabled. Storybook has no tile server, so these stories use OpenStreetMap's
 * public raster tiles to exercise the same code path a titiler TileJSON drives.
 */
const meta: Meta<typeof RasterTileLayer> = {
  component: RasterTileLayer,
  title: "Map/RasterTileLayer",
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
type Story = StoryObj<typeof RasterTileLayer>;

const OSM_TILES = ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"];

export const FullOpacity: Story = {
  args: { tiles: OSM_TILES },
};

export const Blended: Story = {
  args: { tiles: OSM_TILES, opacity: 0.45 },
};

/** Bounded to a footprint, the way an item preview arrives from TileJSON. */
export const BoundedToAnItem: Story = {
  args: { tiles: OSM_TILES, bounds: [-100, 30, -90, 40], opacity: 0.9 },
};
