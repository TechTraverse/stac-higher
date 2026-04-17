import type { StacCollection, StacItem } from "@shared/lib/stac-api/types";

export const mockCollection: StacCollection = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "sentinel-2-l2a",
  title: "Sentinel-2 Level-2A",
  description:
    "Sentinel-2 is a wide-swath, high-resolution, multi-spectral imaging mission supporting Copernicus Land Monitoring studies. This collection provides analysis-ready surface reflectance data.",
  license: "proprietary",
  extent: {
    spatial: {
      bbox: [[-180, -90, 180, 90]],
    },
    temporal: {
      interval: [["2015-06-23T00:00:00Z", null]],
    },
  },
  keywords: ["sentinel", "esa", "copernicus", "satellite", "multispectral", "optical"],
  providers: [
    {
      name: "ESA",
      roles: ["producer"],
      url: "https://sentinel.esa.int/web/sentinel/missions/sentinel-2",
    },
  ],
  links: [
    { href: "https://example.com/collections/sentinel-2-l2a", rel: "self" },
  ],
  assets: {
    thumbnail: {
      href: "https://example.com/thumbnail.png",
      type: "image/png",
      title: "Thumbnail",
      roles: ["thumbnail"],
    },
  },
};

export const mockCollectionMinimal: StacCollection = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "my-simple-collection",
  description: "A minimal collection with only required fields.",
  license: "CC-BY-4.0",
  extent: {
    spatial: { bbox: [[-180, -90, 180, 90]] },
    temporal: { interval: [["2020-01-01T00:00:00Z", "2023-12-31T23:59:59Z"]] },
  },
  links: [],
};

export const mockCollectionNoTitle: StacCollection = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "no-title-collection",
  description: "This collection has no title, so the ID should be displayed.",
  license: "MIT",
  extent: {
    spatial: { bbox: [[-10, -10, 10, 10]] },
    temporal: { interval: [["2022-01-01T00:00:00Z", null]] },
  },
  links: [],
};

export const mockCollectionManyKeywords: StacCollection = {
  type: "Collection",
  stac_version: "1.0.0",
  id: "many-keywords",
  title: "Many Keywords Collection",
  description: "This collection has many keywords to test badge overflow.",
  license: "Apache-2.0",
  extent: {
    spatial: { bbox: [[-180, -90, 180, 90]] },
    temporal: { interval: [["2021-01-01T00:00:00Z", null]] },
  },
  keywords: ["sar", "radar", "microwave", "flood", "change-detection", "time-series", "global", "cloud"],
  links: [],
};

export const mockItem: StacItem = {
  type: "Feature",
  stac_version: "1.0.0",
  id: "S2A_MSIL2A_20230601T100001_N0509_R122_T32UNE_20230601T134819",
  geometry: {
    type: "Polygon",
    coordinates: [
      [
        [8.5, 47.3],
        [9.8, 47.3],
        [9.8, 48.1],
        [8.5, 48.1],
        [8.5, 47.3],
      ],
    ],
  },
  bbox: [8.5, 47.3, 9.8, 48.1],
  properties: {
    datetime: "2023-06-01T10:00:01Z",
    "eo:cloud_cover": 12.5,
    platform: "sentinel-2a",
  },
  links: [{ href: "https://example.com/items/S2A_MSIL2A_20230601", rel: "self" }],
  assets: {
    B04: {
      href: "https://example.com/B04.tif",
      type: "image/tiff; application=geotiff; profile=cloud-optimized",
      title: "Red (Band 4)",
      roles: ["data"],
    },
    B08: {
      href: "https://example.com/B08.tif",
      type: "image/tiff; application=geotiff; profile=cloud-optimized",
      title: "NIR (Band 8)",
      roles: ["data"],
    },
    thumbnail: {
      href: "https://example.com/thumbnail.jpg",
      type: "image/jpeg",
      title: "Thumbnail",
      roles: ["thumbnail"],
    },
  },
  collection: "sentinel-2-l2a",
};

export const mockItemDateRange: StacItem = {
  type: "Feature",
  stac_version: "1.0.0",
  id: "composite-2023-q2",
  geometry: {
    type: "Point",
    coordinates: [9.1, 47.7],
  },
  bbox: [9.1, 47.7, 9.1, 47.7],
  properties: {
    datetime: null,
    start_datetime: "2023-04-01T00:00:00Z",
    end_datetime: "2023-06-30T23:59:59Z",
  },
  links: [],
  assets: {
    composite: {
      href: "https://example.com/composite.tif",
      type: "image/tiff",
      title: "Composite",
      roles: ["data"],
    },
  },
  collection: "composites",
};

export const mockItemNoGeometry: StacItem = {
  type: "Feature",
  stac_version: "1.0.0",
  id: "tabular-data-2023",
  geometry: null,
  properties: {
    datetime: "2023-01-15T08:30:00Z",
  },
  links: [],
  assets: {
    data: {
      href: "https://example.com/data.csv",
      type: "text/csv",
      title: "Data CSV",
      roles: ["data"],
    },
    metadata: {
      href: "https://example.com/metadata.json",
      type: "application/json",
      title: "Metadata",
      roles: ["metadata"],
    },
  },
  collection: "tabular",
};

export const mockItemManyAssets: StacItem = {
  type: "Feature",
  stac_version: "1.0.0",
  id: "many-assets-item",
  geometry: { type: "Point", coordinates: [0, 0] },
  properties: { datetime: "2023-03-10T12:00:00Z" },
  links: [],
  assets: {
    B02: { href: "https://example.com/B02.tif", type: "image/tiff", title: "Blue", roles: ["data"] },
    B03: { href: "https://example.com/B03.tif", type: "image/tiff", title: "Green", roles: ["data"] },
    B04: { href: "https://example.com/B04.tif", type: "image/tiff", title: "Red", roles: ["data"] },
    B08: { href: "https://example.com/B08.tif", type: "image/tiff", title: "NIR", roles: ["data"] },
    SCL: { href: "https://example.com/SCL.tif", type: "image/tiff", title: "Scene Classification", roles: ["data"] },
    thumbnail: { href: "https://example.com/thumb.jpg", type: "image/jpeg", title: "Thumbnail", roles: ["thumbnail"] },
  },
  collection: "sentinel-2-l2a",
};
