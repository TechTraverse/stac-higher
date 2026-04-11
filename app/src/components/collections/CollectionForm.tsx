import { useState, useCallback } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useStore } from "@nanostores/react";
import { $activeEndpoint } from "@/stores/endpointStore";
import {
  useCreateCollection,
  useUpdateCollection,
} from "@/lib/query/collections";
import {
  collectionFormSchema,
  type CollectionFormData,
} from "@/lib/stac-api/schemas";
import type { StacCollection } from "@/lib/stac-api/types";
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";
import { JsonViewer } from "@/components/shared/JsonViewer";
import { BboxInput } from "@/components/shared/BboxInput";
import { StacMap } from "@/components/map/StacMap";
import { ExtentLayer } from "@/components/map/ExtentLayer";
import { bboxToLngLatBounds } from "@/lib/map/bbox";
import type { MapMouseEvent } from "react-map-gl/maplibre";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { ArrowLeft, Plus, Trash2, Save } from "lucide-react";
import { toast } from "sonner";

const COMMON_LICENSES = [
  "proprietary",
  "CC-BY-4.0",
  "CC-BY-SA-4.0",
  "CC0-1.0",
  "Apache-2.0",
  "MIT",
  "other",
];

const PROVIDER_ROLES = ["licensor", "producer", "processor", "host"] as const;

function formToStacCollection(
  data: CollectionFormData,
  stacVersion = "1.0.0",
): StacCollection {
  const assets: Record<string, { href: string; type?: string; title?: string; description?: string; roles?: string[] }> = {};
  data.assets?.forEach(({ key, asset }) => {
    assets[key] = asset;
  });

  return {
    type: "Collection",
    stac_version: stacVersion,
    id: data.id,
    title: data.title || undefined,
    description: data.description,
    license: data.license,
    extent: {
      spatial: { bbox: [data.spatial_bbox] },
      temporal: {
        interval: [
          [
            data.temporal_start || null,
            data.temporal_end || null,
          ],
        ],
      },
    },
    keywords: data.keywords?.filter(Boolean) || undefined,
    providers: data.providers?.filter((p) => p.name) || undefined,
    links: data.links ?? [],
    assets: Object.keys(assets).length > 0 ? assets : undefined,
  };
}

function stacCollectionToForm(collection: StacCollection): CollectionFormData {
  const bbox = collection.extent?.spatial?.bbox?.[0] ?? [0, 0, 0, 0];
  const interval = collection.extent?.temporal?.interval?.[0] ?? [null, null];

  return {
    id: collection.id,
    title: collection.title ?? "",
    description: collection.description,
    license: collection.license,
    spatial_bbox: bbox,
    temporal_start: interval[0] ?? "",
    temporal_end: interval[1] ?? "",
    keywords: collection.keywords ?? [],
    providers: collection.providers?.map((p) => ({
      name: p.name,
      description: p.description ?? "",
      roles: p.roles ?? [],
      url: p.url ?? "",
    })) ?? [],
    assets: collection.assets
      ? Object.entries(collection.assets).map(([key, asset]) => ({
          key,
          asset,
        }))
      : [],
    links: collection.links ?? [],
  };
}

interface CollectionFormInnerProps {
  existingCollection?: StacCollection;
}

function CollectionFormInner({ existingCollection }: CollectionFormInnerProps) {
  const endpoint = useStore($activeEndpoint);
  const endpointUrl = endpoint?.url ?? "";
  const isEdit = !!existingCollection;

  const createMutation = useCreateCollection(endpointUrl);
  const updateMutation = useUpdateCollection(endpointUrl);

  const form = useForm<CollectionFormData>({
    resolver: zodResolver(collectionFormSchema) as any,
    defaultValues: existingCollection
      ? stacCollectionToForm(existingCollection)
      : {
          id: "",
          title: "",
          description: "",
          license: "proprietary",
          spatial_bbox: [-180, -90, 180, 90],
          temporal_start: "",
          temporal_end: "",
          keywords: [],
          providers: [],
          assets: [],
          links: [],
        },
  });

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = form;

  const providers = useFieldArray({ control: form.control, name: "providers" });
  const assets = useFieldArray({ control: form.control, name: "assets" });

  const [bboxCorner, setBboxCorner] = useState<[number, number] | null>(null);
  const [bboxDrawing, setBboxDrawing] = useState(false);

  const handleBboxMapClick = useCallback(
    (e: MapMouseEvent) => {
      if (!bboxDrawing) return;
      const lngLat: [number, number] = [e.lngLat.lng, e.lngLat.lat];
      if (!bboxCorner) {
        setBboxCorner(lngLat);
      } else {
        const [minLng, maxLng] =
          bboxCorner[0] < lngLat[0]
            ? [bboxCorner[0], lngLat[0]]
            : [lngLat[0], bboxCorner[0]];
        const [minLat, maxLat] =
          bboxCorner[1] < lngLat[1]
            ? [bboxCorner[1], lngLat[1]]
            : [lngLat[1], bboxCorner[1]];
        setValue("spatial_bbox", [minLng, minLat, maxLng, maxLat]);
        setBboxCorner(null);
        setBboxDrawing(false);
      }
    },
    [bboxDrawing, bboxCorner, setValue],
  );

  const watchAll = watch();
  const previewCollection = formToStacCollection(
    watchAll,
    existingCollection?.stac_version,
  );

  const onSubmit = (data: CollectionFormData) => {
    const collection = formToStacCollection(
      data,
      existingCollection?.stac_version,
    );

    if (isEdit) {
      updateMutation.mutate(
        { collectionId: data.id, data: collection },
        {
          onSuccess: () => {
            toast.success("Collection updated");
            window.location.href = `/collections/${encodeURIComponent(data.id)}`;
          },
          onError: (err) => toast.error(`Update failed: ${err.message}`),
        },
      );
    } else {
      createMutation.mutate(collection, {
        onSuccess: () => {
          toast.success("Collection created");
          window.location.href = `/collections/${encodeURIComponent(data.id)}`;
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
          <a href="/collections" className="hover:text-foreground transition-colors inline-flex items-center gap-1">
            <ArrowLeft className="h-3.5 w-3.5" />
            Collections
          </a>
          <span>/</span>
          <span className="text-foreground">
            {isEdit ? `Edit ${existingCollection.id}` : "New Collection"}
          </span>
        </div>

        <h1 className="text-2xl font-bold mb-6">
          {isEdit ? "Edit Collection" : "Create Collection"}
        </h1>

        <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
          <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Basic Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="id">Collection ID</Label>
                  <Input
                    id="id"
                    {...register("id")}
                    disabled={isEdit}
                    placeholder="my-collection"
                  />
                  {errors.id && (
                    <p className="text-xs text-destructive">{errors.id.message}</p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label htmlFor="title">Title</Label>
                  <Input
                    id="title"
                    {...register("title")}
                    placeholder="My Collection"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="description">Description</Label>
                  <Textarea
                    id="description"
                    {...register("description")}
                    placeholder="A detailed description of this collection..."
                    rows={4}
                  />
                  {errors.description && (
                    <p className="text-xs text-destructive">
                      {errors.description.message}
                    </p>
                  )}
                </div>
                <div className="space-y-2">
                  <Label>License</Label>
                  <Select
                    value={watchAll.license}
                    onValueChange={(v) => setValue("license", v)}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select license" />
                    </SelectTrigger>
                    <SelectContent>
                      {COMMON_LICENSES.map((l) => (
                        <SelectItem key={l} value={l}>
                          {l}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {errors.license && (
                    <p className="text-xs text-destructive">
                      {errors.license.message}
                    </p>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-base">Spatial Extent</CardTitle>
                <Button
                  type="button"
                  variant={bboxDrawing ? "default" : "outline"}
                  size="sm"
                  onClick={() => {
                    setBboxDrawing(!bboxDrawing);
                    setBboxCorner(null);
                  }}
                >
                  {bboxDrawing ? "Cancel Draw" : "Draw on Map"}
                </Button>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="h-[250px] rounded-lg overflow-hidden border border-border relative">
                  {bboxDrawing && (
                    <div className="absolute bottom-2 left-2 z-10 bg-background/90 backdrop-blur rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground">
                      {!bboxCorner
                        ? "Click first corner of bounding box"
                        : "Click opposite corner to finish"}
                    </div>
                  )}
                  <StacMap
                    initialBounds={bboxToLngLatBounds(watchAll.spatial_bbox)}
                    onClick={handleBboxMapClick}
                  >
                    <ExtentLayer bbox={watchAll.spatial_bbox} />
                  </StacMap>
                </div>
                <BboxInput
                  value={watchAll.spatial_bbox}
                  onChange={(bbox) => setValue("spatial_bbox", bbox)}
                />
                {errors.spatial_bbox && (
                  <p className="text-xs text-destructive mt-2">
                    {errors.spatial_bbox.message}
                  </p>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Temporal Extent</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="temporal_start">Start Date</Label>
                    <Input
                      id="temporal_start"
                      type="datetime-local"
                      {...register("temporal_start")}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="temporal_end">End Date</Label>
                    <Input
                      id="temporal_end"
                      type="datetime-local"
                      {...register("temporal_end")}
                    />
                    <p className="text-xs text-muted-foreground">
                      Leave blank for ongoing collections
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between space-y-0">
                <CardTitle className="text-base">Providers</CardTitle>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    providers.append({ name: "", description: "", roles: [], url: "" })
                  }
                >
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  Add
                </Button>
              </CardHeader>
              <CardContent className="space-y-4">
                {providers.fields.length === 0 && (
                  <p className="text-sm text-muted-foreground">No providers added.</p>
                )}
                {providers.fields.map((field, index) => (
                  <div key={field.id} className="space-y-3 p-3 rounded-lg border border-border">
                    <div className="flex items-center justify-between">
                      <Label className="text-xs font-medium">
                        Provider {index + 1}
                      </Label>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={() => providers.remove(index)}
                        aria-label="Remove provider"
                      >
                        <Trash2 className="h-3.5 w-3.5 text-destructive" />
                      </Button>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label className="text-xs">Name</Label>
                        <Input
                          {...register(`providers.${index}.name`)}
                          placeholder="Organization name"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">URL</Label>
                        <Input
                          {...register(`providers.${index}.url`)}
                          placeholder="https://..."
                        />
                      </div>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Roles</Label>
                      <div className="flex gap-2 flex-wrap">
                        {PROVIDER_ROLES.map((role) => {
                          const currentRoles = watchAll.providers?.[index]?.roles ?? [];
                          const checked = currentRoles.includes(role);
                          return (
                            <label key={role} className="flex items-center gap-1.5 text-xs cursor-pointer">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={(e) => {
                                  const next = e.target.checked
                                    ? [...currentRoles, role]
                                    : currentRoles.filter((r) => r !== role);
                                  setValue(`providers.${index}.roles`, next);
                                }}
                                className="rounded"
                              />
                              {role}
                            </label>
                          );
                        })}
                      </div>
                    </div>
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
                          placeholder="thumbnail"
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
                        placeholder="image/png"
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
                    ? "Update Collection"
                    : "Create Collection"}
              </Button>
              <a href="/collections">
                <Button type="button" variant="outline">
                  Cancel
                </Button>
              </a>
            </div>
          </form>

          <div className="hidden lg:block">
            <div className="sticky top-20">
              <JsonViewer data={previewCollection} title="JSON Preview" defaultOpen />
            </div>
          </div>
        </div>
      </main>
    </>
  );
}

export function CollectionFormPage({
  existingCollection,
}: {
  existingCollection?: StacCollection;
}) {
  return (
    <QueryProvider>
      <CollectionFormInner existingCollection={existingCollection} />
    </QueryProvider>
  );
}
