import type { WidgetProps } from "@rjsf/utils";
import { Input } from "@shared/components/ui/input";

export function NumberWidget({
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
    <Input
      id={id}
      type="number"
      value={value ?? ""}
      required={required}
      disabled={disabled || readonly}
      placeholder={placeholder}
      onChange={(e) => {
        const v = e.target.value;
        onChange(v === "" ? undefined : Number(v));
      }}
      onBlur={(e) => onBlur(id, e.target.value === "" ? undefined : Number(e.target.value))}
      onFocus={(e) => onFocus(id, e.target.value)}
    />
  );
}
