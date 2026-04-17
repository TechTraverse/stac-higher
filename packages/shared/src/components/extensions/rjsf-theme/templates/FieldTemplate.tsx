import type { FieldTemplateProps } from "@rjsf/utils";
import { Label } from "@shared/components/ui/label";

export function FieldTemplate({
  id,
  label,
  required,
  rawDescription,
  children,
  errors,
  hidden,
  displayLabel,
}: FieldTemplateProps) {
  if (hidden) return <div className="hidden">{children}</div>;

  return (
    <div className="space-y-1.5">
      {displayLabel && label && (
        <Label htmlFor={id} className="text-sm">
          {label}
          {required && <span className="text-destructive ml-0.5">*</span>}
        </Label>
      )}
      {children}
      {rawDescription && (
        <p className="text-xs text-muted-foreground">{rawDescription}</p>
      )}
      {errors && (
        <div className="space-y-0.5">
          {errors}
        </div>
      )}
    </div>
  );
}
