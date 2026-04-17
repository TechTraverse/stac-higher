import type { WidgetProps } from "@rjsf/utils";
import { Switch } from "@shared/components/ui/switch";
import { Label } from "@shared/components/ui/label";

export function CheckboxWidget({
  id,
  value,
  disabled,
  readonly,
  label,
  onChange,
}: WidgetProps) {
  return (
    <div className="flex items-center gap-2">
      <Switch
        id={id}
        checked={!!value}
        disabled={disabled || readonly}
        onCheckedChange={(checked) => onChange(checked)}
      />
      {label && (
        <Label htmlFor={id} className="text-sm cursor-pointer">
          {label}
        </Label>
      )}
    </div>
  );
}
