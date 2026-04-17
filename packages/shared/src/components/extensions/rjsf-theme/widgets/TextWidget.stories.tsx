import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { TextWidget } from "./TextWidget";
import { makeWidgetProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Widgets/TextWidget",
  component: TextWidget,
};

export default meta;
type Story = StoryObj;

export const Default: Story = {
  render: () => <TextWidget {...makeWidgetProps({ onChange: fn() })} />,
};

export const WithValue: Story = {
  render: () => (
    <TextWidget {...makeWidgetProps({ value: "sentinel-2-l2a", label: "Collection ID", onChange: fn() })} />
  ),
};

export const WithPlaceholder: Story = {
  render: () => (
    <TextWidget {...makeWidgetProps({ placeholder: "e.g. my-collection-id", onChange: fn() })} />
  ),
};

export const Disabled: Story = {
  render: () => (
    <TextWidget {...makeWidgetProps({ value: "read-only-value", disabled: true, onChange: fn() })} />
  ),
};

export const Readonly: Story = {
  render: () => (
    <TextWidget {...makeWidgetProps({ value: "readonly-value", readonly: true, onChange: fn() })} />
  ),
};
