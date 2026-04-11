import { useForm, useFieldArray, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import { useCreateItem, useUpdateItem } from "@/lib/query/items";
import { itemFormSchema, type ItemFormData } from "@/lib/stac-api/schemas";
import type { StacItem } from "@/lib/stac-api/types";
import { geometryToBbox } from "@/lib/map/bbox";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { ItemGeometryEditor } from "./ItemGeometryEditor";
import { JsonViewer } from "@/components/shared/JsonViewer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ArrowLeft, Plus, Trash2, Save } from "lucide-react";
import { toast } from "sonner";

function formToStacItem(
  data: ItemFormData,
  collectionId: string,
  stacVersion = "1.0.0",
): StacItem {
  const assets: Record<string, { href: string; type?: string; title?: string; description?: string; roles?: string[] }> = {};
  data.assets?.forEach(({ key, asset }) => {
    assets[key] = asset;
  });

  const additionalProps: Record<string, unknown> = {};
  data.properties?.forEach(({ key, value }) => {
    if (key) {
      try {
        additionalProps[key] = JSON.parse(value);
      } catch {
        additionalProps[key] = value;
      }
    }
  });

  const bbox = data.geometry ? geometryToBbox(data.geometry) : undefined;

  return {
    type: "Feature",
    stac_version: stacVersion,
    id: data.id,
    geometry: data.geometry,
    bbox: bbox ?? undefined,
    properties: {
      datetime: data.datetime ? new Date(data.datetime).toISOString() : null,
      ...additionalProps,
    },
    links: [],
    assets,
    collection: collectionId,
  };
}

function stacItemToForm(item: StacItem): ItemFormData {
  const { datetime, start_datetime, end_datetime, ...rest } = item.properties;

  const dtValue = datetime
    ? datetime.slice(0, 16)
    : start_datetime
      ? String(start_datetime).slice(0, 16)
      : "";

  const properties = Object.entries(rest)
    .filter(([key]) => !key.startsWith("_"))
    .map(([key, value]) => ({
      key,
      value: typeof value === "string" ? value : JSON.stringify(value),
    }));

  return {
    id: item.id,
    datetime: dtValue,
    geometry: item.geometry,
    properties,
    assets: Object.entries(item.assets).map(([key, asset]) => ({
      key,
      asset,
    })),
  };
}

interface ItemFormInnerProps {
  collectionId: string;
  existingItem?: StacItem;
}

function ItemFormInner({ collectionId, existingItem }: ItemFormInnerProps) {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
  const isEdit = !!existingItem;

  const createMutation = useCreateItem(endpointUrl, collectionId);
  const updateMutation = useUpdateItem(endpointUrl, collectionId);

  const form = useForm<ItemFormData>({
    resolver: zodResolver(itemFormSchema) as any,
    defaultValues: existingItem
      ? stacItemToForm(existingItem)
      : {
          id: "",
          datetime: new Date().toISOString().slice(0, 16),
          geometry: null,
          properties: [],
          assets: [],
        },
  });

  const {
    register,
    handleSubmit,
    watch,
    control,
    formState: { errors, isSubmitting },
  } = form;

  const properties = useFieldArray({ control, name: "properties" });
  const assets = useFieldArray({ control, name: "assets" });

  const watchAll = watch();
  const previewItem = formToStacItem(
    watchAll,
    collectionId,
    existingItem?.stac_version,
  );

  const onSubmit = (data: ItemFormData) => {
    const item = formToStacItem(data, collectionId, existingItem?.stac_version);

    if (isEdit) {
      updateMutation.mutate(
        { itemId: data.id, data: item },
        {
          onSuccess: () => {
            toast.success("Item updated");
            window.location.href = `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(data.id)}`;
          },
          onError: (err) => toast.error(`Update failed: ${err.message}`),
        },
      );
    } else {
      createMutation.mutate(item, {
        onSuccess: () => {
          toast.success("Item created");
          window.location.href = `/collections/${encodeURIComponent(collectionId)}/items/${encodeURIComponent(data.id)}`;
        },
        onError: (err) => toast.error(`Create failed: ${err.message}`),
      });
    }
  };

  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        <div className="flex items-center gap-2 mb-4 text-sm text-muted-foreground">
          <a href="/collections" className="hover:text-foreground transition-colors">
            Collections
          </a>
          <span>/</span>
          <a
            href={`/collections/${encodeURIComponent(collectionId)}`}
            className="hover:text-foreground transition-colors"
          >
            {collectionId}
          </a>
          <span>/</span>
          <a
            href={`/collections/${encodeURIComponent(collectionId)}/items`}
            className="hover:text-foreground transition-colors"
          >
            Items
          </a>
          <span>/</span>
          <span className="text-foreground">
            {isEdit ? `Edit ${existingItem.id}` : "New Item"}
          </span>
        </div>

        <h1 className="text-2xl font-bold mb-6">
          {isEdit ? "Edit Item" : "Create Item"}
        </h1>

        <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Basic Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="item-id">Item ID</Label>
                  <Input
                    id="item-id"
                    {...register("id")}
                    disabled={isEdit}
                    placeholder="my-item-001"
                  />
                  {errors.id && (
                    <p className="text-xs text-destructive">{errors.id.message}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="item-datetime">Datetime</Label>
                  <Input
                    id="item-datetime"
                    type="datetime-local"
                    {...register("datetime")}
                  />
                  {errors.datetime && (
                    <p className="text-xs text-destructive">
                      {errors.datetime.message}
                    </p>
                  )}
                </div>
                <div className="text-xs text-muted-foreground">
                  Collection:{" "}
                  <span className="font-mono">{collectionId}</span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Geometry</CardTitle>
              </CardHeader>
              <CardContent>
                <Controller
                  control={control}
                  name="geometry"
                  render={({ field }) => (
                    <ItemGeometryEditor
                      value={field.value}
                      onChange={field.onChange}
                    />
                  )}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-base">Properties</CardTitle>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => properties.append({ key: "", value: "" })}
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Add
                </Button>
              </CardHeader>
              <CardContent className="space-y-3">
                {properties.fields.length === 0 && (
                  <p className="text-sm text-muted-foreground">
                    No additional properties. Datetime is already included.
                  </p>
                )}
                {properties.fields.map((field, index) => (
                  <div key={field.id} className="flex items-start gap-2">
                    <div className="flex-1 space-y-1">
                      <Input
                        {...register(`properties.${index}.key`)}
                        placeholder="Property name"
                        className="text-xs"
                      />
                    </div>
                    <div className="flex-1 space-y-1">
                      <Input
                        {...register(`properties.${index}.value`)}
                        placeholder="Value"
                        className="text-xs"
                      />
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 shrink-0"
                      onClick={() => properties.remove(index)}
                      aria-label="Remove property"
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-base">Assets</CardTitle>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    assets.append({
                      key: "",
                      asset: { href: "", type: "", title: "", roles: [] },
                    })
                  }
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Add
                </Button>
              </CardHeader>
              <CardContent className="space-y-4">
                {assets.fields.length === 0 && (
                  <p className="text-sm text-muted-foreground">No assets added.</p>
                )}
                {assets.fields.map((field, index) => (
                  <div key={field.id} className="space-y-3 p-3 rounded-lg border border-border">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs font-medium">
                        Asset {index + 1}
                      </Label>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => assets.remove(index)}
                        aria-label="Remove asset"
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Key</Label>
                        <Input
                          {...register(`assets.${index}.key`)}
                          placeholder="data"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">Title</Label>
                        <Input
                          {...register(`assets.${index}.asset.title`)}
                          placeholder="Asset title"
                        />
                      </div>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">URL</Label>
                      <Input
                        {...register(`assets.${index}.asset.href`)}
                        placeholder="https://..."
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Media Type</Label>
                      <Input
                        {...register(`assets.${index}.asset.type`)}
                        placeholder="image/tiff"
                      />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            <div className="flex items-center gap-3">
              <Button type="submit" disabled={isSubmitting}>
                <Save className="h-4 w-4 mr-1.5" />
                {isSubmitting
                  ? "Saving..."
                  : isEdit
                    ? "Update Item"
                    : "Create Item"}
              </Button>
              <a href={`/collections/${encodeURIComponent(collectionId)}/items`}>
                <Button type="button" variant="outline">
                  Cancel
                </Button>
              </a>
            </div>
          </form>

          <div className="hidden lg:block">
            <div className="sticky top-20">
              <JsonViewer data={previewItem} title="JSON Preview" defaultOpen />
            </div>
          </div>
        </div>
      </main>
    </>
  );
}

export function ItemFormPage({
  collectionId,
  existingItem,
}: {
  collectionId: string;
  existingItem?: StacItem;
}) {
  return (
    <QueryProvider>
      <ItemFormInner collectionId={collectionId} existingItem={existingItem} />
    </QueryProvider>
  );
}
