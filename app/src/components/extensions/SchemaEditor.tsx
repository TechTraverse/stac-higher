import { useFieldArray, useFormContext } from "react-hook-form";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@stac-higher/shared";
import { Plus, Trash2 } from "lucide-react";
import type { ExtensionFormValues } from "@/lib/extensions/schemas";

const PROPERTY_TYPES = [
  { value: "string", label: "String" },
  { value: "number", label: "Number" },
  { value: "integer", label: "Integer" },
  { value: "boolean", label: "Boolean" },
  { value: "array", label: "Array" },
] as const;

const ARRAY_ITEM_TYPES = [
  { value: "string", label: "String" },
  { value: "number", label: "Number" },
  { value: "integer", label: "Integer" },
  { value: "boolean", label: "Boolean" },
] as const;

const NONE_VALUE = "__none__";

const STRING_FORMATS = [
  "date-time",
  "date",
  "time",
  "uri",
  "email",
  "hostname",
  "ipv4",
  "ipv6",
];

function emptyProperty(): ExtensionFormValues["properties"][number] {
  return {
    name: "",
    type: "string",
    description: "",
    required: false,
  };
}

export function SchemaEditor() {
  const {
    control,
    register,
    setValue,
    watch,
    formState: { errors },
  } = useFormContext<ExtensionFormValues>();

  const { fields, append, remove } = useFieldArray({
    control,
    name: "properties",
  });

  const watchAll = watch();

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-base">Properties</CardTitle>
          {errors.properties?.root && (
            <p className="text-xs text-destructive mt-1">
              {errors.properties.root.message}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => append(emptyProperty())}
        >
          <Plus className="h-3.5 w-3.5 mr-1" />
          Add Property
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {fields.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No properties added yet.
          </p>
        )}
        {fields.map((field, index) => {
          const propType = watchAll.properties?.[index]?.type ?? "string";
          const prefix = watchAll.prefix || "prefix";
          const propName = watchAll.properties?.[index]?.name || "property";
          const isRequired = watchAll.properties?.[index]?.required ?? false;

          return (
            <div
              key={field.id}
              className="space-y-3 p-4 rounded-lg border border-border"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary" className="font-mono text-xs">
                    {prefix}:{propName}
                  </Badge>
                  {isRequired && (
                    <Badge variant="destructive" className="text-xs">
                      required
                    </Badge>
                  )}
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() => remove(index)}
                  aria-label="Remove property"
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </Button>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label className="text-xs">Property Name</Label>
                  <Input
                    {...register(`properties.${index}.name`)}
                    placeholder="cloud_cover"
                  />
                  {errors.properties?.[index]?.name && (
                    <p className="text-xs text-destructive">
                      {errors.properties[index]?.name?.message}
                    </p>
                  )}
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Type</Label>
                  <Select
                    value={propType}
                    onValueChange={(v) =>
                      setValue(
                        `properties.${index}.type`,
                        v as ExtensionFormValues["properties"][number]["type"],
                      )
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PROPERTY_TYPES.map((t) => (
                        <SelectItem key={t.value} value={t.value}>
                          {t.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-1">
                <Label className="text-xs">Description</Label>
                <Input
                  {...register(`properties.${index}.description`)}
                  placeholder="Describe this property..."
                />
              </div>

              <div className="flex items-center gap-2">
                <Switch
                  id={`required-${index}`}
                  checked={isRequired}
                  onCheckedChange={(v) =>
                    setValue(`properties.${index}.required`, v)
                  }
                />
                <Label htmlFor={`required-${index}`} className="text-xs cursor-pointer">
                  Required
                </Label>
              </div>

              {(propType === "string" ||
                propType === "number" ||
                propType === "integer") && (
                <div className="space-y-3 pt-1 border-t border-border/50">
                  <p className="text-xs text-muted-foreground font-medium">
                    Constraints
                  </p>

                  {propType === "string" && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Format</Label>
                        <Select
                          value={watchAll.properties?.[index]?.format || NONE_VALUE}
                          onValueChange={(v) =>
                            setValue(
                              `properties.${index}.format`,
                              v === NONE_VALUE ? undefined : v,
                            )
                          }
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="None" />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={NONE_VALUE}>None</SelectItem>
                            {STRING_FORMATS.map((f) => (
                              <SelectItem key={f} value={f}>
                                {f}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Default Value</Label>
                        <Input
                          {...register(`properties.${index}.default`)}
                          placeholder='"value"'
                        />
                      </div>
                    </div>
                  )}

                  {(propType === "number" || propType === "integer") && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Minimum</Label>
                        <Input
                          {...register(`properties.${index}.minimum`)}
                          placeholder="0"
                          type="number"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Maximum</Label>
                        <Input
                          {...register(`properties.${index}.maximum`)}
                          placeholder="100"
                          type="number"
                        />
                      </div>
                    </div>
                  )}

                  <div className="space-y-1">
                    <Label className="text-xs">Enum Values</Label>
                    <Input
                      {...register(`properties.${index}.enumValues`)}
                      placeholder="value1, value2, value3"
                    />
                    <p className="text-xs text-muted-foreground">
                      Comma-separated list of allowed values
                    </p>
                  </div>
                </div>
              )}

              {propType === "array" && (
                <div className="space-y-3 pt-1 border-t border-border/50">
                  <p className="text-xs text-muted-foreground font-medium">
                    Array Configuration
                  </p>
                  <div className="space-y-1">
                    <Label className="text-xs">Item Type</Label>
                    <Select
                      value={
                        watchAll.properties?.[index]?.arrayItemType ?? "string"
                      }
                      onValueChange={(v) =>
                        setValue(
                          `properties.${index}.arrayItemType`,
                          v as ExtensionFormValues["properties"][number]["arrayItemType"],
                        )
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ARRAY_ITEM_TYPES.map((t) => (
                          <SelectItem key={t.value} value={t.value}>
                            {t.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              )}

              {propType === "boolean" && (
                <div className="space-y-1 pt-1 border-t border-border/50">
                  <Label className="text-xs">Default Value</Label>
                  <Select
                    value={watchAll.properties?.[index]?.default || NONE_VALUE}
                    onValueChange={(v) =>
                      setValue(
                        `properties.${index}.default`,
                        v === NONE_VALUE ? undefined : v,
                      )
                    }
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="None" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NONE_VALUE}>None</SelectItem>
                      <SelectItem value="true">true</SelectItem>
                      <SelectItem value="false">false</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
          );
        })}

        {fields.length === 0 && (
          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={() => append(emptyProperty())}
          >
            <Plus className="h-4 w-4 mr-1.5" />
            Add First Property
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
