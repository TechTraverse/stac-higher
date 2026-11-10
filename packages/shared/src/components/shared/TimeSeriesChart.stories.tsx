import type { Meta, StoryObj } from "@storybook/react-vite";
import { TimeSeriesChart, type TimeSeriesPoint } from "./TimeSeriesChart";

const T0 = Date.parse("2026-10-03T17:00:00Z");
const series = (values: (number | null)[]): TimeSeriesPoint[] =>
  values.map((value, i) => ({ t: T0 + i * 5 * 60_000, value }));

const meta: Meta<typeof TimeSeriesChart> = {
  component: TimeSeriesChart,
  title: "Shared/TimeSeriesChart",
  args: { ariaLabel: "CMI at 38.5000, -97.1235", unit: "K" },
};

export default meta;
type Story = StoryObj<typeof TimeSeriesChart>;

export const Continuous: Story = {
  args: { points: series([251.5, 252.2, 253.9, 255.1, 254.4, 252.8, 251.9, 250.7]) },
};

export const WithGaps: Story = {
  args: { points: series([251.5, 252.2, null, null, 255.1, 254.4, null, 251.9, 250.7]) },
};

export const IsolatedPoint: Story = {
  args: { points: series([null, 251.5, null, 253.1, 254.0, null]) },
};

export const AllMissing: Story = {
  args: { points: series([null, null, null]) },
};

export const Flat: Story = {
  args: { points: series([250, 250, 250, 250]) },
};
