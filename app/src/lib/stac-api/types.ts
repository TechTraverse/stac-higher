export interface StacLink {
  href: string;
  rel: string;
  type?: string;
  title?: string;
}

export interface StacAsset {
  href: string;
  type?: string;
  title?: string;
  description?: string;
  roles?: string[];
}

export interface StacProvider {
  name: string;
  description?: string;
  roles?: ("licensor" | "producer" | "processor" | "host")[];
  url?: string;
}

export interface StacSpatialExtent {
  bbox: number[][];
}

export interface StacTemporalExtent {
  interval: (string | null)[][];
}

export interface StacExtent {
  spatial: StacSpatialExtent;
  temporal: StacTemporalExtent;
}

export interface StacCollection {
  type: "Collection";
  stac_version: string;
  stac_extensions?: string[];
  id: string;
  title?: string;
  description: string;
  license: string;
  extent: StacExtent;
  keywords?: string[];
  providers?: StacProvider[];
  summaries?: Record<string, unknown>;
  links: StacLink[];
  assets?: Record<string, StacAsset>;
}

export interface StacItemProperties {
  datetime: string | null;
  start_datetime?: string;
  end_datetime?: string;
  [key: string]: unknown;
}

export interface StacItem {
  type: "Feature";
  stac_version: string;
  stac_extensions?: string[];
  id: string;
  geometry: GeoJSON.Geometry | null;
  bbox?: number[];
  properties: StacItemProperties;
  links: StacLink[];
  assets: Record<string, StacAsset>;
  collection?: string;
}

export interface StacCollectionsResponse {
  collections: StacCollection[];
  links: StacLink[];
  numberMatched?: number;
  numberReturned?: number;
}

export interface StacItemCollection {
  type: "FeatureCollection";
  features: StacItem[];
  links: StacLink[];
  numberMatched?: number;
  numberReturned?: number;
  context?: {
    returned: number;
    matched: number;
    limit: number;
  };
}

export interface StacSearchBody {
  collections?: string[];
  ids?: string[];
  bbox?: number[];
  intersects?: GeoJSON.Geometry;
  datetime?: string;
  limit?: number;
  token?: string;
  fields?: {
    includes?: string[];
    excludes?: string[];
  };
  sortby?: Array<{
    field: string;
    direction: "asc" | "desc";
  }>;
  filter?: Record<string, unknown>;
  "filter-lang"?: string;
}

export interface StacLandingPage {
  type: string;
  id: string;
  title?: string;
  description: string;
  stac_version: string;
  conformsTo?: string[];
  links: StacLink[];
}

export class StacApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: string,
  ) {
    super(message);
    this.name = "StacApiError";
  }
}
