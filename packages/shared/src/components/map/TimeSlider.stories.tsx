import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import { TimeSlider } from "./TimeSlider";

/**
 * The play/scrub controls under a product's animated raster preview. The
 * component is controlled, so the stories hold the index themselves.
 */
const meta: Meta<typeof TimeSlider> = {
  component: TimeSlider,
  title: "Map/TimeSlider",
  decorators: [
    (Story) => (
      <div className="w-[42rem] max-w-full p-4">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj<typeof TimeSlider>;

/** GOES cadence: one frame every five minutes. */
function goesLabels(count: number): string[] {
  const start = Date.parse("2026-09-04T02:11:17.300Z");
  return Array.from({ length: count }, (_, i) => {
    const iso = new Date(start + i * 5 * 60_000).toISOString();
    return `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC`;
  });
}

function Controlled({ labels, fps }: { labels: string[]; fps?: number }) {
  const [index, setIndex] = useState(labels.length - 1);
  return <TimeSlider labels={labels} index={index} onIndexChange={setIndex} fps={fps} />;
}

export const FiftyFrames: Story = {
  render: () => <Controlled labels={goesLabels(50)} />,
};

/** Slow enough to read each timestep as it passes. */
export const SlowPlayback: Story = {
  render: () => <Controlled labels={goesLabels(50)} fps={1} />,
};

/** A single timestep leaves nothing to play. */
export const OneFrame: Story = {
  render: () => <Controlled labels={goesLabels(1)} />,
};
