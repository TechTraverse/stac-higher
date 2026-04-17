import type { WidgetProps, FieldTemplateProps, ArrayFieldTemplateProps, ObjectFieldTemplateProps } from "@rjsf/utils";

/**
 * Creates a minimal WidgetProps stub for use in Storybook stories.
 * Only includes fields consumed by the custom widgets.
 */
export function makeWidgetProps(overrides: Partial<WidgetProps> = {}): WidgetProps {
  return {
    id: "story-field",
    value: undefined,
    required: false,
    disabled: false,
    readonly: false,
    placeholder: "",
    label: "Field Label",
    onChange: () => {},
    onBlur: () => {},
    onFocus: () => {},
    options: {},
    schema: {},
    uiSchema: {},
    formContext: {},
    autofocus: false,
    rawErrors: [],
    registry: {} as WidgetProps["registry"],
    ...overrides,
  } as unknown as WidgetProps;
}

/**
 * Creates a minimal FieldTemplateProps stub for use in Storybook stories.
 */
export function makeFieldTemplateProps(overrides: Partial<FieldTemplateProps> = {}): FieldTemplateProps {
  return {
    id: "story-field",
    label: "Field Label",
    required: false,
    hidden: false,
    displayLabel: true,
    rawDescription: "",
    errors: null,
    children: null,
    schema: {},
    uiSchema: {},
    formContext: {},
    registry: {} as FieldTemplateProps["registry"],
    ...overrides,
  } as unknown as FieldTemplateProps;
}

/**
 * Creates a minimal ArrayFieldTemplateProps stub for use in Storybook stories.
 */
export function makeArrayFieldTemplateProps(
  overrides: Partial<ArrayFieldTemplateProps> = {},
): ArrayFieldTemplateProps {
  return {
    title: "",
    items: [],
    canAdd: true,
    onAddClick: () => {},
    schema: {},
    uiSchema: {},
    formContext: {},
    registry: {} as ArrayFieldTemplateProps["registry"],
    ...overrides,
  } as unknown as ArrayFieldTemplateProps;
}

/**
 * Creates a minimal ObjectFieldTemplateProps stub for use in Storybook stories.
 */
export function makeObjectFieldTemplateProps(
  overrides: Partial<ObjectFieldTemplateProps> = {},
): ObjectFieldTemplateProps {
  return {
    title: "",
    description: "",
    properties: [],
    schema: {},
    uiSchema: {},
    formContext: {},
    registry: {} as ObjectFieldTemplateProps["registry"],
    ...overrides,
  } as unknown as ObjectFieldTemplateProps;
}
