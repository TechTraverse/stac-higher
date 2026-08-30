/**
 * I-67: extension-schema preparation for RJSF — STAC-template fields
 * extraction, remote $ref inlining (bundled GeoJSON offline; anything else
 * through the resolver seam), and graceful degradation (raw-JSON fallback)
 * when a $ref genuinely can't be resolved. No live network anywhere.
 */
import { describe, it, expect, vi } from "vitest";
import {
  prepareExtensionSchema,
  extractFieldsSchema,
  collectRemoteRefBases,
  RAW_JSON_FIELD,
} from "@/lib/extensions/ref-resolve";
import {
  projectionSchemaFixture,
  GEOJSON_GEOMETRY_URL,
  PROJJSON_SCHEMA_URL,
} from "./helpers/projection-fixture";

type Obj = Record<string, any>;

const offlineResolver = () => vi.fn(async (url: string): Promise<unknown> => {
  throw new Error(`offline: ${url}`);
});

describe("extractFieldsSchema", () => {
  it("hoists definitions.fields to the root for STAC-template schemas", () => {
    const extracted = extractFieldsSchema(projectionSchemaFixture) as Obj;
    expect(extracted.type).toBe("object");
    expect(extracted.properties["proj:code"]).toBeDefined();
    // Extension identity survives for the panel title.
    expect(extracted.title).toBe("Projection Extension");
    // Local #/definitions/... refs must stay resolvable.
    expect(extracted.definitions.fields).toBeDefined();
    // The template's own-prefix closure doesn't help a rendered form.
    expect(extracted.patternProperties).toBeUndefined();
    expect(extracted.additionalProperties).toBeUndefined();
  });

  it("passes non-template schemas (locally-built extensions) through unchanged", () => {
    const flat = {
      type: "object",
      title: "My Extension",
      properties: { "ext:field": { type: "string" } },
    };
    expect(extractFieldsSchema(flat)).toBe(flat);
  });
});

describe("collectRemoteRefBases", () => {
  it("finds remote $refs and ignores local ones", () => {
    const bases = collectRemoteRefBases(projectionSchemaFixture);
    expect(bases).toContain(GEOJSON_GEOMETRY_URL);
    expect(bases).toContain(PROJJSON_SCHEMA_URL);
    expect(bases.some((b) => b.startsWith("#"))).toBe(false);
  });

  it("strips fragments to the base document URL", () => {
    const bases = collectRemoteRefBases({
      properties: { a: { $ref: "https://example.com/doc.json#/definitions/x" } },
    });
    expect(bases).toEqual(["https://example.com/doc.json"]);
  });
});

describe("prepareExtensionSchema — projection extension (the I-67 case)", () => {
  it("inlines the GeoJSON geometry $ref from the bundle without any fetch", async () => {
    const resolve = offlineResolver();
    const { schema, unresolved } = await prepareExtensionSchema(
      projectionSchemaFixture,
      resolve,
    );

    const geometry = (schema as Obj).properties["proj:geometry"];
    expect(geometry.$ref).toBeUndefined();
    expect(geometry.oneOf?.map((m: Obj) => m.title)).toContain("GeoJSON Polygon");
    // geojson.org is bundled — never fetched, works offline.
    expect(resolve).not.toHaveBeenCalledWith(GEOJSON_GEOMETRY_URL);
    expect(unresolved).not.toContain(GEOJSON_GEOMETRY_URL);
  });

  it("degrades an unresolvable $ref to a raw-JSON field instead of failing", async () => {
    const { schema, uiSchema, unresolved } = await prepareExtensionSchema(
      projectionSchemaFixture,
      offlineResolver(),
    );

    // proj:projjson references proj.org, unreachable offline.
    expect(unresolved).toContain(PROJJSON_SCHEMA_URL);
    const projjson = (schema as Obj).properties["proj:projjson"];
    expect(projjson.title).toBe("Coordinate Reference System in PROJJSON format");
    expect(projjson.description).toContain(PROJJSON_SCHEMA_URL);
    expect((uiSchema as Obj)["proj:projjson"]).toEqual({ "ui:field": RAW_JSON_FIELD });
  });

  it("leaves no remote $refs anywhere in the prepared schema", async () => {
    const { schema } = await prepareExtensionSchema(
      projectionSchemaFixture,
      offlineResolver(),
    );
    expect(collectRemoteRefBases(schema)).toEqual([]);
  });

  it("does not mutate the input schema", async () => {
    const input = structuredClone(projectionSchemaFixture);
    await prepareExtensionSchema(input, offlineResolver());
    expect(input).toEqual(projectionSchemaFixture);
  });
});

describe("prepareExtensionSchema — resolver-based inlining", () => {
  const fieldsTemplate = (properties: Obj): Obj => ({
    title: "Test Extension",
    definitions: { fields: { type: "object", properties } },
  });

  it("inlines a resolvable remote $ref, stripping $id/$schema and keeping siblings", async () => {
    const resolve = vi.fn(async () => ({
      $schema: "http://json-schema.org/draft-07/schema#",
      $id: "https://example.com/thing.json",
      type: "string",
      title: "Thing",
    }));
    const { schema, unresolved } = await prepareExtensionSchema(
      fieldsTemplate({
        "ext:thing": { title: "Overridden", $ref: "https://example.com/thing.json" },
      }),
      resolve,
    );
    const thing = (schema as Obj).properties["ext:thing"];
    expect(thing).toEqual({ type: "string", title: "Overridden" });
    expect(unresolved).toEqual([]);
    expect(resolve).toHaveBeenCalledWith("https://example.com/thing.json");
  });

  it("follows JSON-pointer fragments into the resolved document", async () => {
    const resolve = vi.fn(async () => ({
      definitions: { foo: { type: "number", title: "Foo" } },
    }));
    const { schema } = await prepareExtensionSchema(
      fieldsTemplate({
        "ext:foo": { $ref: "https://example.com/defs.json#/definitions/foo" },
      }),
      resolve,
    );
    expect((schema as Obj).properties["ext:foo"]).toEqual({
      type: "number",
      title: "Foo",
    });
  });

  it("resolves remote refs revealed by earlier inlining (nested documents)", async () => {
    const docs: Record<string, unknown> = {
      "https://example.com/a.json": {
        type: "object",
        properties: { inner: { $ref: "https://example.com/b.json" } },
      },
      "https://example.com/b.json": { type: "boolean" },
    };
    const resolve = vi.fn(async (url: string) => {
      if (url in docs) return docs[url];
      throw new Error("unknown");
    });
    const { schema, unresolved } = await prepareExtensionSchema(
      fieldsTemplate({ "ext:a": { $ref: "https://example.com/a.json" } }),
      resolve,
    );
    expect((schema as Obj).properties["ext:a"].properties.inner).toEqual({
      type: "boolean",
    });
    expect(unresolved).toEqual([]);
  });

  it("degrades a resolved document whose internal refs cannot be rebased", async () => {
    const resolve = vi.fn(async () => ({
      type: "object",
      properties: { x: { $ref: "#/definitions/x" } },
      definitions: { x: { type: "string" } },
    }));
    const { schema, uiSchema, unresolved } = await prepareExtensionSchema(
      fieldsTemplate({ "ext:complex": { $ref: "https://example.com/complex.json" } }),
      resolve,
    );
    expect(unresolved).toEqual(["https://example.com/complex.json"]);
    expect((schema as Obj).properties["ext:complex"].$ref).toBeUndefined();
    expect((uiSchema as Obj)["ext:complex"]).toEqual({ "ui:field": RAW_JSON_FIELD });
  });

  it("neutralizes an unresolvable $ref at a non-nameable position without touching siblings", async () => {
    const { schema, uiSchema, unresolved } = await prepareExtensionSchema(
      fieldsTemplate({
        "ext:arr": {
          type: "array",
          title: "List",
          items: { $ref: "https://example.com/gone.json" },
        },
      }),
      offlineResolver(),
    );
    const arr = (schema as Obj).properties["ext:arr"];
    // The array property itself survives; only the items node is neutralized.
    expect(arr.type).toBe("array");
    expect(arr.title).toBe("List");
    expect(arr.items.$ref).toBeUndefined();
    expect(arr.items.description).toContain("https://example.com/gone.json");
    expect(unresolved).toEqual(["https://example.com/gone.json"]);
    expect((uiSchema as Obj)["ext:arr"]).toBeUndefined();
    expect(collectRemoteRefBases(schema)).toEqual([]);
  });

  it("contains a resolver that rejects for every URL (fully offline, unknown refs)", async () => {
    const { schema, unresolved } = await prepareExtensionSchema(
      fieldsTemplate({
        "ext:one": { $ref: "https://example.com/one.json" },
        "ext:two": { $ref: "https://example.com/two.json" },
      }),
      offlineResolver(),
    );
    expect(unresolved.sort()).toEqual([
      "https://example.com/one.json",
      "https://example.com/two.json",
    ]);
    expect(collectRemoteRefBases(schema)).toEqual([]);
  });
});
