import type { Meta, StoryObj } from "@storybook/react-vite";
import type { ObjectFieldTemplateProps } from "@rjsf/utils";
import { ObjectFieldTemplate } from "./ObjectFieldTemplate";
import { makeObjectFieldTemplateProps } from "../rjsf-stories.utils";

const fieldRow = (label: string, placeholder: string) => (
  <div className="space-y-1">
    <label className="text-sm text-foreground">{label}</label>
    <input
      className="w-full border border-border rounded px-2 py-1 text-sm bg-background"
      placeholder={placeholder}
    />
  </div>
);

const mockProperties: ObjectFieldTemplateProps["properties"] = [
  { name: "platform", content: fieldRow("Platform", "sentinel-2a"), disabled: false, hidden: false, readonly: false },
  { name: "cloud_cover", content: fieldRow("Cloud Cover %", "0–100"), disabled: false, hidden: false, readonly: false },
  { name: "gsd", content: fieldRow("Ground Sampling Distance (m)", "10"), disabled: false, hidden: false, readonly: false },
] as unknown as ObjectFieldTemplateProps["properties"];

const meta: Meta = {
  title: "Extensions/RJSF Templates/ObjectFieldTemplate",
  component: ObjectFieldTemplate,
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

export const RootLevel: Story = {
  render: () => (
    <ObjectFieldTemplate
      {...makeObjectFieldTemplateProps({
        title: "",
        properties: mockProperties,
      })}
    />
  ),
};

export const NestedWithTitle: Story = {
  render: () => (
    <ObjectFieldTemplate
      {...makeObjectFieldTemplateProps({
        title: "EO Extension",
        description: "Fields from the Electro-Optical STAC extension.",
        properties: mockProperties,
      })}
    />
  ),
};

export const EmptyProperties: Story = {
  render: () => (
    <ObjectFieldTemplate
      {...makeObjectFieldTemplateProps({
        title: "Empty Section",
        description: "No properties defined yet.",
        properties: [],
      })}
    />
  ),
};
