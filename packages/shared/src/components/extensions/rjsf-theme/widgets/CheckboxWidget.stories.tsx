import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { CheckboxWidget } from "./CheckboxWidget";
import { makeWidgetProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Widgets/CheckboxWidget",
  component: CheckboxWidget,
};

export default meta;
type Story = StoryObj;

export const Unchecked: Story = {
  render: () => (
    <CheckboxWidget {...makeWidgetProps({ value: false, label: "Enable feature", onChange: fn() })} />
  ),
};

export const Checked: Story = {
  render: () => (
    <CheckboxWidget {...makeWidgetProps({ value: true, label: "Enable feature", onChange: fn() })} />
  ),
};

export const NoLabel: Story = {
  render: () => (
    <CheckboxWidget {...makeWidgetProps({ value: true, label: "", onChange: fn() })} />
  ),
};

export const Disabled: Story = {
  render: () => (
    <CheckboxWidget {...makeWidgetProps({ value: true, disabled: true, label: "Disabled toggle", onChange: fn() })} />
  ),
};
