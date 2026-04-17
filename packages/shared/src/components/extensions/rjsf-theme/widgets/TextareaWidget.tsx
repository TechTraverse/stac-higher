import type { WidgetProps } from "@rjsf/utils";
import { Textarea } from "@shared/components/ui/textarea";

export function TextareaWidget({
  id,
  value,
  required,
  disabled,
  readonly,
  placeholder,
  onChange,
  onBlur,
  onFocus,
}: WidgetProps) {
  return (
    <Textarea
      id={id}
      value={value ?? ""}
      required={required}
      disabled={disabled || readonly}
      placeholder={placeholder}
      rows={3}
      onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
      onBlur={(e) => onBlur(id, e.target.value)}
      onFocus={(e) => onFocus(id, e.target.value)}
    />
  );
}
