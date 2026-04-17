import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { NumberWidget } from "./NumberWidget";
import { makeWidgetProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Widgets/NumberWidget",
  component: NumberWidget,
};

export default meta;
type Story = StoryObj;

export const Default: Story = {
  render: () => <NumberWidget {...makeWidgetProps({ onChange: fn() })} />,
};

export const WithValue: Story = {
  render: () => (
    <NumberWidget {...makeWidgetProps({ value: 42, label: "Cloud Cover %", onChange: fn() })} />
  ),
};

export const WithPlaceholder: Story = {
  render: () => (
    <NumberWidget {...makeWidgetProps({ placeholder: "0.0 – 100.0", onChange: fn() })} />
  ),
};

export const Disabled: Story = {
  render: () => (
    <NumberWidget {...makeWidgetProps({ value: 12.5, disabled: true, onChange: fn() })} />
  ),
};
