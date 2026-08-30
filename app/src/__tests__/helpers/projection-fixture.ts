/**
 * Trimmed-but-faithful copy of the STAC projection extension schema v2.0.0
 * (https://stac-extensions.github.io/projection/v2.0.0/schema.json) — the
 * shape every `raster_auto`-ingested item references (I-67): a oneOf
 * Item/Collection envelope, `definitions.fields` carrying the editable
 * `proj:*` properties, a remote GeoJSON `$ref` (`proj:geometry`) and a remote
 * PROJJSON `$ref` (`proj:projjson`) whose document is not inlinable.
 */

export const PROJECTION_SCHEMA_URL =
  "https://stac-extensions.github.io/projection/v2.0.0/schema.json";
export const GEOJSON_GEOMETRY_URL = "https://geojson.org/schema/Geometry.json";
export const PROJJSON_SCHEMA_URL =
  "https://proj.org/schemas/v0.7/projjson.schema.json";

export const projectionSchemaFixture = {
  $schema: "http://json-schema.org/draft-07/schema#",
  $id: PROJECTION_SCHEMA_URL,
  title: "Projection Extension",
  description: "STAC Projection Extension for STAC Items.",
  oneOf: [
    {
      $comment: "This is the schema for STAC Items.",
      allOf: [
        { $ref: "#/definitions/stac_extensions" },
        {
          type: "object",
          required: ["type", "properties", "assets"],
          properties: {
            type: { const: "Feature" },
            properties: { $ref: "#/definitions/fields" },
            assets: {
              type: "object",
              additionalProperties: { $ref: "#/definitions/fields" },
            },
          },
        },
      ],
    },
    {
      $comment: "This is the schema for STAC Collections.",
      allOf: [
        {
          type: "object",
          required: ["type"],
          properties: {
            type: { const: "Collection" },
            assets: {
              type: "object",
              additionalProperties: { $ref: "#/definitions/fields" },
            },
          },
        },
        { $ref: "#/definitions/stac_extensions" },
      ],
    },
  ],
  definitions: {
    stac_extensions: {
      type: "object",
      required: ["stac_extensions"],
      properties: {
        stac_extensions: {
          type: "array",
          contains: { const: PROJECTION_SCHEMA_URL },
        },
      },
    },
    fields: {
      type: "object",
      properties: {
        "proj:code": {
          title: "Projection code",
          type: ["string", "null"],
        },
        "proj:projjson": {
          title: "Coordinate Reference System in PROJJSON format",
          oneOf: [{ $ref: PROJJSON_SCHEMA_URL }, { type: "null" }],
        },
        "proj:geometry": {
          $ref: GEOJSON_GEOMETRY_URL,
        },
        "proj:bbox": {
          title: "Extent",
          type: "array",
          oneOf: [
            { minItems: 4, maxItems: 4 },
            { minItems: 6, maxItems: 6 },
          ],
          items: { type: "number" },
        },
        "proj:shape": {
          title: "Shape",
          type: "array",
          minItems: 2,
          maxItems: 2,
          items: { type: "integer" },
        },
        "proj:transform": {
          title: "Transform",
          type: "array",
          oneOf: [
            { minItems: 6, maxItems: 6 },
            { minItems: 9, maxItems: 9 },
          ],
          items: { type: "number" },
        },
      },
      patternProperties: {
        "^(?!proj:)": {},
      },
      additionalProperties: false,
    },
  },
};

/** Item properties as a pipeline-ingested (raster_auto) item carries them. */
export const projItemProperties = {
  datetime: "2026-08-01T00:00:00Z",
  "proj:code": "EPSG:32633",
  "proj:shape": [512, 512],
  "proj:transform": [10, 0, 399960, 0, -10, 4900020],
  "proj:geometry": {
    type: "Polygon",
    coordinates: [
      [
        [0, 0],
        [1, 0],
        [1, 1],
        [0, 0],
      ],
    ],
  },
};
