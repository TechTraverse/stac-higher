/**
 * Bundled GeoJSON JSON Schemas (I-67).
 *
 * STAC extension schemas commonly reference https://geojson.org/schema/*.json
 * (e.g. the projection extension's `proj:geometry`). Bundling these locally
 * keeps the item/collection edit forms working offline and avoids a network
 * round-trip for the by-far most common remote `$ref`.
 *
 * Content mirrors https://geojson.org/schema/Geometry.json (draft-07), with
 * `$id`/`$schema` stripped so the definitions can be inlined into a host
 * schema without confusing ajv's reference resolution.
 */

const bboxSchema = {
  type: "array",
  minItems: 4,
  items: { type: "number" },
} as const;

const position = {
  type: "array",
  minItems: 2,
  items: { type: "number" },
} as const;

const positionArray = { type: "array", items: position } as const;

const lineStringCoordinates = {
  type: "array",
  minItems: 2,
  items: position,
} as const;

const linearRing = {
  type: "array",
  minItems: 4,
  items: position,
} as const;

const polygonCoordinates = { type: "array", items: linearRing } as const;

const GEOJSON_POINT = {
  title: "GeoJSON Point",
  type: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { type: "string", enum: ["Point"] },
    coordinates: position,
    bbox: bboxSchema,
  },
};

const GEOJSON_LINESTRING = {
  title: "GeoJSON LineString",
  type: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { type: "string", enum: ["LineString"] },
    coordinates: lineStringCoordinates,
    bbox: bboxSchema,
  },
};

const GEOJSON_POLYGON = {
  title: "GeoJSON Polygon",
  type: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { type: "string", enum: ["Polygon"] },
    coordinates: polygonCoordinates,
    bbox: bboxSchema,
  },
};

const GEOJSON_MULTIPOINT = {
  title: "GeoJSON MultiPoint",
  type: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { type: "string", enum: ["MultiPoint"] },
    coordinates: positionArray,
    bbox: bboxSchema,
  },
};

const GEOJSON_MULTILINESTRING = {
  title: "GeoJSON MultiLineString",
  type: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { type: "string", enum: ["MultiLineString"] },
    coordinates: { type: "array", items: lineStringCoordinates },
    bbox: bboxSchema,
  },
};

const GEOJSON_MULTIPOLYGON = {
  title: "GeoJSON MultiPolygon",
  type: "object",
  required: ["type", "coordinates"],
  properties: {
    type: { type: "string", enum: ["MultiPolygon"] },
    coordinates: { type: "array", items: polygonCoordinates },
    bbox: bboxSchema,
  },
};

const GEOJSON_GEOMETRY = {
  title: "GeoJSON Geometry",
  oneOf: [
    GEOJSON_POINT,
    GEOJSON_LINESTRING,
    GEOJSON_POLYGON,
    GEOJSON_MULTIPOINT,
    GEOJSON_MULTILINESTRING,
    GEOJSON_MULTIPOLYGON,
  ],
};

const GEOJSON_GEOMETRYCOLLECTION = {
  title: "GeoJSON GeometryCollection",
  type: "object",
  required: ["type", "geometries"],
  properties: {
    type: { type: "string", enum: ["GeometryCollection"] },
    geometries: { type: "array", items: GEOJSON_GEOMETRY },
    bbox: bboxSchema,
  },
};

/** Remote `$ref` URLs that resolve locally, without any fetch. */
export const BUNDLED_SCHEMAS: Record<string, unknown> = {
  "https://geojson.org/schema/Geometry.json": GEOJSON_GEOMETRY,
  "https://geojson.org/schema/GeometryCollection.json": GEOJSON_GEOMETRYCOLLECTION,
  "https://geojson.org/schema/Point.json": GEOJSON_POINT,
  "https://geojson.org/schema/LineString.json": GEOJSON_LINESTRING,
  "https://geojson.org/schema/Polygon.json": GEOJSON_POLYGON,
  "https://geojson.org/schema/MultiPoint.json": GEOJSON_MULTIPOINT,
  "https://geojson.org/schema/MultiLineString.json": GEOJSON_MULTILINESTRING,
  "https://geojson.org/schema/MultiPolygon.json": GEOJSON_MULTIPOLYGON,
};
