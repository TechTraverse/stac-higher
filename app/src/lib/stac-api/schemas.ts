import { z } from "zod";

export const stacLinkSchema = z.object({
  href: z.string().url("Must be a valid URL"),
  rel: z.string().min(1, "Relation type is required"),
  type: z.string().optional(),
  title: z.string().optional(),
});

export const stacAssetSchema = z.object({
  href: z.string().min(1, "Asset URL is required"),
  type: z.string().optional(),
  title: z.string().optional(),
  description: z.string().optional(),
  roles: z.array(z.string()).optional(),
});

export const stacProviderSchema = z.object({
  name: z.string().min(1, "Provider name is required"),
  description: z.string().optional(),
  roles: z
    .array(z.enum(["licensor", "producer", "processor", "host"]))
    .optional(),
  url: z.string().url("Must be a valid URL").optional().or(z.literal("")),
});

const bboxSchema = z
  .array(z.number())
  .length(4, "Bbox must have 4 values: [minLng, minLat, maxLng, maxLat]");

export const collectionFormSchema = z.object({
  id: z
    .string()
    .min(1, "ID is required")
    .regex(
      /^[a-zA-Z0-9_-]+$/,
      "ID must contain only letters, numbers, hyphens, and underscores",
    ),
  title: z.string().optional(),
  description: z.string().min(1, "Description is required"),
  license: z.string().min(1, "License is required"),
  spatial_bbox: bboxSchema,
  temporal_start: z.string().optional(),
  temporal_end: z.string().optional(),
  keywords: z.array(z.string()).optional(),
  providers: z.array(stacProviderSchema).optional(),
  assets: z
    .array(
      z.object({
        key: z.string().min(1, "Asset key is required"),
        asset: stacAssetSchema,
      }),
    )
    .optional(),
  links: z.array(stacLinkSchema).optional(),
});

export type CollectionFormData = z.infer<typeof collectionFormSchema>;

export const geojsonGeometrySchema = z.any().refine(
  (val): val is GeoJSON.Geometry =>
    val === null ||
    val === undefined ||
    (typeof val === "object" &&
      val !== null &&
      "type" in val &&
      "coordinates" in val),
  { message: "Must be a valid GeoJSON geometry" },
) as z.ZodType<GeoJSON.Geometry>;

export const itemFormSchema = z.object({
  id: z.string().min(1, "ID is required"),
  datetime: z.string().min(1, "Datetime is required"),
  geometry: z.any().nullable() as z.ZodType<GeoJSON.Geometry | null>,
  properties: z.array(
    z.object({
      key: z.string().min(1),
      value: z.string(),
    }),
  ).optional(),
  assets: z
    .array(
      z.object({
        key: z.string().min(1, "Asset key is required"),
        asset: stacAssetSchema,
      }),
    )
    .optional(),
});

export type ItemFormData = z.infer<typeof itemFormSchema>;
