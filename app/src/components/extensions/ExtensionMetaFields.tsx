import { useFormContext } from "react-hook-form";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Textarea,
} from "@stac-higher/shared";
import type { ExtensionFormValues } from "@/lib/extensions/schemas";

interface ExtensionMetaFieldsProps {
  isEdit: boolean;
}

export function ExtensionMetaFields({ isEdit }: ExtensionMetaFieldsProps) {
  const {
    register,
    formState: { errors },
  } = useFormContext<ExtensionFormValues>();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Basic Information</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="name">Extension Name</Label>
          <Input
            id="name"
            {...register("name")}
            placeholder="My Extension"
          />
          {errors.name && (
            <p className="text-xs text-destructive">{errors.name.message}</p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="prefix">Prefix</Label>
            <Input
              id="prefix"
              {...register("prefix")}
              placeholder="my_ext"
              disabled={isEdit}
            />
            {errors.prefix && (
              <p className="text-xs text-destructive">{errors.prefix.message}</p>
            )}
            <p className="text-xs text-muted-foreground">
              Lowercase, alphanumeric + underscores
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="version">Version</Label>
            <Input
              id="version"
              {...register("version")}
              placeholder="1.0.0"
            />
            {errors.version && (
              <p className="text-xs text-destructive">{errors.version.message}</p>
            )}
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="description">Description</Label>
          <Textarea
            id="description"
            {...register("description")}
            placeholder="Describe what this extension adds..."
            rows={3}
          />
        </div>
      </CardContent>
    </Card>
  );
}
