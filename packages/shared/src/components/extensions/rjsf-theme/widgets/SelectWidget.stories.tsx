import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { SelectWidget } from "./SelectWidget";
import { makeWidgetProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Widgets/SelectWidget",
  component: SelectWidget,
};

export default meta;
type Story = StoryObj;

const platformOptions = [
  { value: "sentinel-2a", label: "Sentinel-2A" },
  { value: "sentinel-2b", label: "Sentinel-2B" },
  { value: "landsat-8", label: "Landsat 8" },
  { value: "landsat-9", label: "Landsat 9" },
];

export const Default: Story = {
  render: () => (
    <div className="w-64">
      <SelectWidget
        {...makeWidgetProps({
          options: { enumOptions: platformOptions },
          onChange: fn(),
        })}
      />
    </div>
  ),
};

export const WithValue: Story = {
  render: () => (
    <div className="w-64">
      <SelectWidget
        {...makeWidgetProps({
          value: "sentinel-2a",
          options: { enumOptions: platformOptions },
          onChange: fn(),
        })}
      />
    </div>
  ),
};

export const Required: Story = {
  render: () => (
    <div className="w-64">
      <SelectWidget
        {...makeWidgetProps({
          required: true,
          options: { enumOptions: platformOptions },
          onChange: fn(),
        })}
      />
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="w-64">
      <SelectWidget
        {...makeWidgetProps({
          value: "landsat-8",
          disabled: true,
          options: { enumOptions: platformOptions },
          onChange: fn(),
        })}
      />
    </div>
  ),
};

export const NoOptions: Story = {
  render: () => (
    <div className="w-64">
      <SelectWidget
        {...makeWidgetProps({
          options: { enumOptions: [] },
          onChange: fn(),
        })}
      />
    </div>
  ),
};
