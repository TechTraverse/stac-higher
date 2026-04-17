import type { ThemeProps } from "@rjsf/core";
import { TextWidget } from "./widgets/TextWidget";
import { TextareaWidget } from "./widgets/TextareaWidget";
import { NumberWidget } from "./widgets/NumberWidget";
import { CheckboxWidget } from "./widgets/CheckboxWidget";
import { SelectWidget } from "./widgets/SelectWidget";
import { FieldTemplate } from "./templates/FieldTemplate";
import { ObjectFieldTemplate } from "./templates/ObjectFieldTemplate";
import { ArrayFieldTemplate } from "./templates/ArrayFieldTemplate";

export const shadcnTheme: ThemeProps = {
  widgets: {
    TextWidget,
    TextareaWidget,
    NumberWidget,
    CheckboxWidget,
    SelectWidget,
    EmailWidget: TextWidget,
    URLWidget: TextWidget,
    PasswordWidget: TextWidget,
    UpDownWidget: NumberWidget,
    RangeWidget: NumberWidget,
  },
  templates: {
    FieldTemplate,
    ObjectFieldTemplate,
    ArrayFieldTemplate,
  },
};
