import type { Meta, StoryObj } from "@storybook/react-vite";
import { fn } from "storybook/test";
import { TextareaWidget } from "./TextareaWidget";
import { makeWidgetProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Widgets/TextareaWidget",
  component: TextareaWidget,
};

export default meta;
type Story = StoryObj;

export const Default: Story = {
  render: () => <TextareaWidget {...makeWidgetProps({ onChange: fn() })} />,
};

export const WithValue: Story = {
  render: () => (
    <TextareaWidget
      {...makeWidgetProps({
        value:
          "Sentinel-2 is a wide-swath, high-resolution, multi-spectral imaging mission supporting Copernicus Land Monitoring studies.",
        onChange: fn(),
      })}
    />
  ),
};

export const WithPlaceholder: Story = {
  render: () => (
    <TextareaWidget
      {...makeWidgetProps({
        placeholder: "Describe this collection in detail...",
        onChange: fn(),
      })}
    />
  ),
};

export const Disabled: Story = {
  render: () => (
    <TextareaWidget
      {...makeWidgetProps({
        value: "This field cannot be edited.",
        disabled: true,
        onChange: fn(),
      })}
    />
  ),
};
