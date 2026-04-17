import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { withTheme } from "@rjsf/core";
import type { IChangeEvent } from "@rjsf/core";
import type { RJSFSchema } from "@rjsf/utils";
import validator from "@rjsf/validator-ajv8";
import {
  shadcnTheme,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@stac-higher/shared";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";

const ThemedForm = withTheme(shadcnTheme);

const UI_SCHEMA = {
  "ui:submitButtonOptions": { norender: true },
};

async function fetchSchema(url: string): Promise<RJSFSchema> {
  const res = await fetch("/api/extensions/resolve-schema", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: string } | null;
    throw new Error(body?.error ?? `Failed to fetch schema: ${res.status}`);
  }
  return res.json();
}

function useExtensionSchema(url: string) {
  return useQuery({
    queryKey: ["extension-schema", url],
    queryFn: () => fetchSchema(url),
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });
}

interface ExtensionFieldProps {
  schemaUrl: string;
  value: Record<string, unknown>;
  onChange: (data: Record<string, unknown>) => void;
}

function ExtensionFieldPanel({ schemaUrl, value, onChange }: ExtensionFieldProps) {
  const { data: schema, isLoading, error } = useExtensionSchema(schemaUrl);
  const [open, setOpen] = useState(true);

  const title =
    (schema?.title as string)?.replace(/ Extension$/, "") ??
    schemaUrl.split("/").filter(Boolean).pop() ??
    "Extension";

  return (
    <Card>
      <CardHeader
        className="flex-row items-center gap-2 space-y-0 cursor-pointer py-3"
        onClick={() => setOpen(!open)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground ml-auto" />}
      </CardHeader>

      {open && (
        <CardContent>
          {isLoading ? (
            <p className="text-xs text-muted-foreground">Loading schema...</p>
          ) : error ? (
            <p className="text-xs text-destructive">
              Failed to load schema:{" "}
              {error instanceof Error ? error.message : "Unknown error"}
            </p>
          ) : schema ? (
            <ThemedForm
              schema={schema}
              uiSchema={UI_SCHEMA}
              formData={value}
              validator={validator}
              onChange={(e: IChangeEvent) => onChange(e.formData ?? {})}
              liveValidate={false}
            />
          ) : null}
        </CardContent>
      )}
    </Card>
  );
}

interface ExtensionFieldsProps {
  schemaUrls: string[];
  value: Record<string, Record<string, unknown>>;
  onChange: (data: Record<string, Record<string, unknown>>) => void;
}

export function ExtensionFields({
  schemaUrls,
  value,
  onChange,
}: ExtensionFieldsProps) {
  if (schemaUrls.length === 0) return null;

  return (
    <div className="space-y-3">
      {schemaUrls.map((url) => (
        <ExtensionFieldPanel
          key={url}
          schemaUrl={url}
          value={value[url] ?? {}}
          onChange={(data) => onChange({ ...value, [url]: data })}
        />
      ))}
    </div>
  );
}
