import type { Meta, StoryObj } from "@storybook/react-vite";
import type { ArrayFieldTemplateProps } from "@rjsf/utils";
import { fn } from "storybook/test";
import { ArrayFieldTemplate } from "./ArrayFieldTemplate";
import { makeArrayFieldTemplateProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Templates/ArrayFieldTemplate",
  component: ArrayFieldTemplate,
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <div className="max-w-sm">
        <Story />
      </div>
    ),
  ],
};

export default meta;
type Story = StoryObj;

export const Empty: Story = {
  render: () => (
    <ArrayFieldTemplate
      {...makeArrayFieldTemplateProps({
        title: "Keywords",
        items: [],
        canAdd: true,
        onAddClick: fn(),
      })}
    />
  ),
};

export const WithItems: Story = {
  render: () => (
    <ArrayFieldTemplate
      {...makeArrayFieldTemplateProps({
        title: "Keywords",
        items: [
          <input key="0" className="w-full border border-border rounded px-2 py-1 text-sm bg-background" defaultValue="sentinel" />,
          <input key="1" className="w-full border border-border rounded px-2 py-1 text-sm bg-background" defaultValue="optical" />,
          <input key="2" className="w-full border border-border rounded px-2 py-1 text-sm bg-background" defaultValue="multispectral" />,
        ] as unknown as ArrayFieldTemplateProps["items"],
        canAdd: true,
        onAddClick: fn(),
      })}
    />
  ),
};

export const CannotAdd: Story = {
  render: () => (
    <ArrayFieldTemplate
      {...makeArrayFieldTemplateProps({
        title: "Fixed List",
        items: [
          <span key="0" className="text-sm">Item 1</span>,
          <span key="1" className="text-sm">Item 2</span>,
        ] as unknown as ArrayFieldTemplateProps["items"],
        canAdd: false,
        onAddClick: fn(),
      })}
    />
  ),
};

export const NoTitle: Story = {
  render: () => (
    <ArrayFieldTemplate
      {...makeArrayFieldTemplateProps({
        items: [
          <input key="0" className="w-full border border-border rounded px-2 py-1 text-sm bg-background" defaultValue="value" />,
        ] as unknown as ArrayFieldTemplateProps["items"],
        canAdd: true,
        onAddClick: fn(),
      })}
    />
  ),
};
