/**
 * Extension-schema preparation for RJSF (I-67).
 *
 * STAC extension schemas (e.g. the projection extension) arrive as a full
 * validation schema — a `oneOf` Item/Collection envelope whose editable field
 * definitions live in `definitions.fields` — and may carry remote `$ref`s
 * (e.g. `https://geojson.org/schema/Geometry.json` for `proj:geometry`).
 * RJSF cannot resolve remote references; handing it the raw schema crashes
 * the whole item edit form ("Could not find a definition for ...").
 *
 * `prepareExtensionSchema` makes such a schema safe to render:
 *
 * 1. **Extract** `definitions.fields` as the root when the schema follows the
 *    STAC extension template (the envelope is meaningless as a form; the
 *    fields object is what the user edits — it matches the repo's
 *    merge-into-`item.properties` save pattern).
 * 2. **Inline** remote `$ref`s: bundled GeoJSON schemas first (offline, no
 *    fetch), then anything resolvable through the provided resolver (the
 *    `/api/extensions/resolve-schema` seam, server-cached). Inlining repeats
 *    for refs revealed by earlier inlining, up to a small pass cap.
 * 3. **Degrade gracefully** for anything left: a `$ref` that cannot be
 *    resolved (offline, dead URL, cyclic, or a document whose internal
 *    references cannot be rebased) never takes down the form. Where the ref
 *    sits under a nameable property, that property becomes a raw-JSON field
 *    (`ui:field: "rawJson"` in the returned uiSchema); elsewhere the ref node
 *    is replaced with a permissive schema so the value passes through
 *    untouched.
 */
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import { BUNDLED_SCHEMAS } from "./geojson-schemas";

export interface PreparedExtensionSchema {
  schema: RJSFSchema;
  /** uiSchema fragments for degraded raw-JSON fields (merge into the form's uiSchema). */
  uiSchema: UiSchema;
  /** Remote `$ref` URLs that could not be resolved/inlined. */
  unresolved: string[];
}

/** Resolves a remote schema URL to its parsed JSON document. */
export type RemoteSchemaResolver = (url: string) => Promise<unknown>;

/** Name the RJSF `fields` registry must map to a raw-JSON editor component. */
export const RAW_JSON_FIELD = "rawJson";

const MAX_PASSES = 4;

/** Value-position keys whose contents are data, not schema — never walked. */
const VALUE_KEYS = new Set(["enum", "const", "default", "examples"]);

type Obj = Record<string, unknown>;

function isObj(x: unknown): x is Obj {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

function remoteRefOf(node: Obj): string | null {
  const ref = node.$ref;
  return typeof ref === "string" && /^https?:\/\//.test(ref) ? ref : null;
}

function splitRef(ref: string): { base: string; fragment: string } {
  const hash = ref.indexOf("#");
  if (hash === -1) return { base: ref, fragment: "" };
  return { base: ref.slice(0, hash), fragment: ref.slice(hash + 1) };
}

/** Generic walk over schema-shaped JSON, skipping data-valued keys. */
function walk(node: unknown, visit: (obj: Obj) => void): void {
  if (Array.isArray(node)) {
    for (const entry of node) walk(entry, visit);
    return;
  }
  if (!isObj(node)) return;
  visit(node);
  for (const [key, value] of Object.entries(node)) {
    if (VALUE_KEYS.has(key)) continue;
    walk(value, visit);
  }
}

/** Collect the base URLs of all remote `$ref`s in the schema. */
export function collectRemoteRefBases(schema: unknown): string[] {
  const bases = new Set<string>();
  walk(schema, (obj) => {
    const ref = remoteRefOf(obj);
    if (ref) bases.add(splitRef(ref).base);
  });
  return [...bases];
}

function hasLocalRefs(node: unknown): boolean {
  let found = false;
  walk(node, (obj) => {
    if (typeof obj.$ref === "string" && obj.$ref.startsWith("#")) found = true;
  });
  return found;
}

/** Resolve a `#/a/b` JSON-pointer fragment inside a document. */
function resolvePointer(doc: unknown, fragment: string): unknown {
  if (fragment === "" || fragment === "/") return doc;
  let current: unknown = doc;
  for (const rawSegment of fragment.replace(/^\//, "").split("/")) {
    const segment = decodeURIComponent(rawSegment)
      .replace(/~1/g, "/")
      .replace(/~0/g, "~");
    if (Array.isArray(current)) {
      current = current[Number(segment)];
    } else if (isObj(current)) {
      current = current[segment];
    } else {
      return undefined;
    }
  }
  return current;
}

/** Strip `$id`/`$schema` so an inlined document can't confuse ref resolution. */
function sanitizeDoc(doc: unknown): unknown {
  if (!isObj(doc)) return doc;
  const clone = structuredClone(doc);
  delete clone.$id;
  delete clone.$schema;
  return clone;
}

/**
 * Hoist `definitions.fields` to the root when the schema follows the STAC
 * extension template. Non-template schemas (e.g. locally-built extensions,
 * which are plain object schemas) pass through unchanged.
 */
export function extractFieldsSchema(raw: unknown): unknown {
  if (!isObj(raw)) return raw;
  const defs = isObj(raw.definitions)
    ? raw.definitions
    : isObj(raw.$defs)
      ? raw.$defs
      : null;
  const fields = defs && isObj(defs.fields) ? defs.fields : null;
  if (!fields || !isObj(fields.properties)) return raw;

  // The STAC template closes `fields` over its own prefix
  // (`additionalProperties: false` + a pattern allowing everything else);
  // neither helps a form that renders only the declared fields.
  const {
    patternProperties: _patternProperties,
    additionalProperties: _additionalProperties,
    ...fieldsRest
  } = fields;

  return {
    ...fieldsRest,
    // Keep the extension's own identity for the panel title.
    ...(typeof raw.title === "string" ? { title: raw.title } : {}),
    ...(typeof raw.description === "string" ? { description: raw.description } : {}),
    // Keep all definitions: `fields` may hold local `#/definitions/...` refs.
    definitions: defs,
  };
}

type Resolution = { status: "ok"; doc: unknown } | { status: "failed" };

/**
 * Replace every remote `$ref` whose target is available and self-contained.
 * Returns the number of replacements (callers loop until quiescent).
 */
function inlinePass(schema: unknown, resolutions: Map<string, Resolution>): number {
  let replaced = 0;
  walk(schema, (node) => {
    const ref = remoteRefOf(node);
    if (!ref) return;
    const { base, fragment } = splitRef(ref);
    const resolution = resolutions.get(base);
    if (!resolution || resolution.status !== "ok") return;
    const target = resolvePointer(resolution.doc, fragment);
    // A target with internal local refs cannot be inlined without rebasing
    // them into the host schema — leave it for the degrade pass.
    if (!isObj(target) || hasLocalRefs(target)) return;
    const siblings: Obj = { ...node };
    delete siblings.$ref;
    for (const key of Object.keys(node)) delete node[key];
    Object.assign(node, structuredClone(target), siblings);
    replaced += 1;
  });
  return replaced;
}

function rawJsonFallbackSchema(title: string, url: string): Obj {
  return {
    title,
    description: `This field's schema references ${url}, which could not be resolved. Edit the value as raw JSON.`,
  };
}

interface DegradeContext {
  /** Chain of property keys from the root; null once the path is not nameable. */
  propPath: string[] | null;
  /** Nearest enclosing direct property (replace target for raw-JSON degrade). */
  prop: { owner: Obj; key: string; title?: string } | null;
}

/**
 * Neutralize every remote `$ref` still present after inlining so nothing can
 * crash RJSF or ajv. Mutates the schema; returns the raw-JSON uiSchema
 * fragments and the list of unresolved URLs.
 */
function degradePass(schema: unknown): { uiSchema: UiSchema; unresolved: string[] } {
  const uiSchema: Obj = {};
  const unresolved = new Set<string>();

  const setRawJsonUi = (path: string[]) => {
    let cursor = uiSchema;
    for (const key of path) {
      if (!isObj(cursor[key])) cursor[key] = {};
      cursor = cursor[key] as Obj;
    }
    cursor["ui:field"] = RAW_JSON_FIELD;
  };

  const visit = (node: unknown, ctx: DegradeContext): void => {
    if (Array.isArray(node)) {
      for (const entry of node) visit(entry, ctx);
      return;
    }
    if (!isObj(node)) return;

    const ref = remoteRefOf(node);
    if (ref) {
      unresolved.add(ref);
      if (ctx.prop && ctx.propPath && ctx.propPath.length > 0) {
        const title =
          (typeof node.title === "string" ? node.title : undefined) ??
          ctx.prop.title ??
          ctx.prop.key;
        ctx.prop.owner[ctx.prop.key] = rawJsonFallbackSchema(title, ref);
        setRawJsonUi(ctx.propPath);
      } else {
        delete node.$ref;
        node.description = `Unresolvable schema reference: ${ref}`;
      }
      return;
    }

    const impure: DegradeContext = { propPath: null, prop: null };
    for (const [key, value] of Object.entries(node)) {
      if (VALUE_KEYS.has(key)) continue;
      if (key === "properties" && isObj(value)) {
        for (const [propKey, propValue] of Object.entries(value)) {
          const path = ctx.propPath ? [...ctx.propPath, propKey] : null;
          visit(propValue, {
            propPath: path,
            prop: path
              ? {
                  owner: value,
                  key: propKey,
                  title: isObj(propValue) && typeof propValue.title === "string" ? propValue.title : undefined,
                }
              : null,
          });
        }
      } else if (key === "oneOf" || key === "anyOf" || key === "allOf") {
        // Combinators don't move the data location — same context.
        visit(value, ctx);
      } else {
        // items, additionalProperties, definitions, dependencies, if/then/
        // else, not, contains, patternProperties, ... — no nameable property.
        visit(value, impure);
      }
    }
  };

  visit(schema, { propPath: [], prop: null });
  return { uiSchema: uiSchema as UiSchema, unresolved: [...unresolved] };
}

/**
 * Prepare a raw extension schema for RJSF rendering. Never rejects because of
 * an unresolvable `$ref` — those degrade per the module docs. `resolveRemote`
 * failures are contained per-URL.
 */
export async function prepareExtensionSchema(
  raw: unknown,
  resolveRemote: RemoteSchemaResolver,
): Promise<PreparedExtensionSchema> {
  const schema = structuredClone(extractFieldsSchema(raw));

  const resolutions = new Map<string, Resolution>(
    Object.entries(BUNDLED_SCHEMAS).map(([url, doc]) => [url, { status: "ok", doc }]),
  );

  for (let pass = 0; pass < MAX_PASSES; pass++) {
    const pending = collectRemoteRefBases(schema).filter((base) => !resolutions.has(base));
    await Promise.all(
      pending.map(async (base) => {
        try {
          resolutions.set(base, { status: "ok", doc: sanitizeDoc(await resolveRemote(base)) });
        } catch {
          resolutions.set(base, { status: "failed" });
        }
      }),
    );
    const replaced = inlinePass(schema, resolutions);
    if (pending.length === 0 && replaced === 0) break;
  }

  const { uiSchema, unresolved } = degradePass(schema);
  return { schema: schema as RJSFSchema, uiSchema, unresolved };
}
