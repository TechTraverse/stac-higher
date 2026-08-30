import { useState } from "react";
import type { FieldProps } from "@rjsf/utils";
import { Label, Textarea } from "@stac-higher/shared";

/**
 * RJSF fallback field for schema nodes whose remote `$ref` could not be
 * resolved (I-67): edits the value as raw JSON instead of taking down the
 * whole form. Registered in the form's `fields` registry as `rawJson` and
 * selected via the uiSchema fragments `prepareExtensionSchema` returns.
 */
export function RawJsonField(props: FieldProps) {
  const { formData, onChange, schema, fieldPathId, disabled, readonly } = props;
  const [text, setText] = useState(() =>
    formData === undefined ? "" : JSON.stringify(formData, null, 2),
  );
  const [invalid, setInvalid] = useState(false);

  const id = fieldPathId?.$id ?? "raw-json-field";
  const title = typeof schema.title === "string" ? schema.title : undefined;
  const description =
    typeof schema.description === "string" ? schema.description : undefined;

  const handleChange = (next: string) => {
    setText(next);
    const path = fieldPathId?.path ?? [];
    if (next.trim() === "") {
      setInvalid(false);
      onChange(undefined, path, undefined, id);
      return;
    }
    try {
      const parsed = JSON.parse(next) as unknown;
      setInvalid(false);
      onChange(parsed, path, undefined, id);
    } catch {
      // Keep the draft locally; don't propagate a broken value.
      setInvalid(true);
    }
  };

  return (
    <div className="space-y-1.5">
      {title && (
        <Label htmlFor={id} className="text-sm">
          {title}
        </Label>
      )}
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
      <Textarea
        id={id}
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        disabled={disabled}
        readOnly={readonly}
        rows={4}
        spellCheck={false}
        className="font-mono text-xs"
        aria-invalid={invalid || undefined}
      />
      {invalid && (
        <p className="text-xs text-destructive">Invalid JSON — value not applied.</p>
      )}
    </div>
  );
}
