import type { WidgetProps } from "@rjsf/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@shared/components/ui/select";

const NONE_VALUE = "__none__";

export function SelectWidget({
  id,
  value,
  required,
  disabled,
  readonly,
  options,
  placeholder,
  onChange,
}: WidgetProps) {
  const { enumOptions } = options as {
    enumOptions?: Array<{ value: unknown; label: string }>;
  };

  const selectValue = value !== undefined ? String(value) : NONE_VALUE;

  return (
    <Select
      value={selectValue}
      onValueChange={(v) => onChange(v === NONE_VALUE ? undefined : v)}
      disabled={disabled || readonly}
      required={required}
    >
      <SelectTrigger id={id}>
        <SelectValue placeholder={placeholder ?? "Select..."} />
      </SelectTrigger>
      <SelectContent>
        {!required && (
          <SelectItem value={NONE_VALUE}>
            <span className="text-muted-foreground italic">None</span>
          </SelectItem>
        )}
        {enumOptions?.map((opt) => (
          <SelectItem key={String(opt.value)} value={String(opt.value)}>
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
