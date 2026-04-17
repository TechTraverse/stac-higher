import type { Meta, StoryObj } from "@storybook/react-vite";
import { FieldTemplate } from "./FieldTemplate";
import { makeFieldTemplateProps } from "../rjsf-stories.utils";

const meta: Meta = {
  title: "Extensions/RJSF Templates/FieldTemplate",
  component: FieldTemplate,
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

export const WithLabel: Story = {
  render: () => (
    <FieldTemplate
      {...makeFieldTemplateProps({
        label: "Platform",
        displayLabel: true,
        children: <input className="w-full border border-border rounded px-2 py-1 text-sm bg-background" placeholder="sentinel-2a" />,
      })}
    />
  ),
};

export const Required: Story = {
  render: () => (
    <FieldTemplate
      {...makeFieldTemplateProps({
        label: "Collection ID",
        required: true,
        displayLabel: true,
        children: <input className="w-full border border-border rounded px-2 py-1 text-sm bg-background" placeholder="my-collection" />,
      })}
    />
  ),
};

export const WithDescription: Story = {
  render: () => (
    <FieldTemplate
      {...makeFieldTemplateProps({
        label: "Cloud Cover",
        displayLabel: true,
        rawDescription: "Percentage of scene covered by clouds (0–100).",
        children: <input type="number" className="w-full border border-border rounded px-2 py-1 text-sm bg-background" placeholder="0" />,
      })}
    />
  ),
};

export const WithErrors: Story = {
  render: () => (
    <FieldTemplate
      {...makeFieldTemplateProps({
        label: "Datetime",
        required: true,
        displayLabel: true,
        errors: <span className="text-xs text-destructive">Required field — must be a valid ISO 8601 date.</span>,
        children: <input className="w-full border border-destructive rounded px-2 py-1 text-sm bg-background" />,
      })}
    />
  ),
};

export const NoLabel: Story = {
  render: () => (
    <FieldTemplate
      {...makeFieldTemplateProps({
        label: "Hidden Label",
        displayLabel: false,
        children: <input className="w-full border border-border rounded px-2 py-1 text-sm bg-background" placeholder="no label shown" />,
      })}
    />
  ),
};

export const Hidden: Story = {
  render: () => (
    <div>
      <p className="text-xs text-muted-foreground mb-2">The field below is hidden (display:none):</p>
      <FieldTemplate
        {...makeFieldTemplateProps({
          hidden: true,
          children: <input className="w-full border border-border rounded px-2 py-1 text-sm bg-background" />,
        })}
      />
    </div>
  ),
};
