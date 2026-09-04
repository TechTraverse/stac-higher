import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { StacMap } from "./StacMap";
import { RasterFrameStack } from "./RasterFrameStack";
import { TimeSlider } from "./TimeSlider";

/**
 * A raster time series shown one frame at a time. Storybook has no tile
 * server, so the "frames" are three public basemaps standing in for three
 * timesteps — enough to see the opacity swap and the invisible lookahead.
 */
const meta: Meta<typeof RasterFrameStack> = {
  component: RasterFrameStack,
  title: "Map/RasterFrameStack",
  parameters: { layout: "fullscreen" },
};

export default meta;
type Story = StoryObj<typeof RasterFrameStack>;

const FRAMES = [
  { key: "osm", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"] },
  { key: "positron", tiles: ["https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"] },
  { key: "dark", tiles: ["https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"] },
];

function Player({ opacity }: { opacity?: number }) {
  const [index, setIndex] = useState(0);
  return (
    <div className="flex flex-col gap-2 p-2">
      <div className="h-[400px] w-full">
        <StacMap>
          <RasterFrameStack id="story" frames={FRAMES} index={index} opacity={opacity} />
        </StacMap>
      </div>
      <TimeSlider labels={FRAMES.map((f) => f.key)} index={index} onIndexChange={setIndex} fps={1} />
    </div>
  );
}

export const Playing: Story = { render: () => <Player /> };
export const Blended: Story = { render: () => <Player opacity={0.5} /> };
