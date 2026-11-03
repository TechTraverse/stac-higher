import type { StacAsset, StacCollection } from "./types";

/**
 * An asset carrying this key is written by a pipeline job (the cube sink's
 * collection writer, Z-5), never by the form: an edit always keeps the stored
 * copy, so a form opened before a publish cannot roll the asset back.
 */
export const PIPELINE_ASSET_MARKER = "stac_higher:cube_sink_id";

/**
 * The document to `PUT` for an edit (#102): a three-way merge of
 *
 * - `loaded`: the document as the form saw it when it opened (form-shaped,
 *   i.e. `formToStacCollection(stacCollectionToForm(original))`),
 * - `edited`: the document the form builds from what the user submitted,
 * - `latest`: the stored document, re-read right before the save.
 *
 * Every key the form does not build passes through from `latest` untouched
 * (`cube:dimensions`, `item_assets`, `renders`, ...). A key the form builds
 * takes the form's value only when the user changed it; otherwise `latest`
 * wins, so a stale form never rolls back another writer. `extent` is merged
 * per part (spatial, temporal), `assets` per asset key, and `stac_extensions`
 * keeps any URL another writer added since the form opened.
 */
export function mergeCollectionEdit({
  loaded,
  edited,
  latest,
}: {
  loaded: StacCollection;
  edited: StacCollection;
  latest: StacCollection;
}): StacCollection {
  const out: Record<string, unknown> = { ...latest };
  const mine = edited as unknown as Record<string, unknown>;
  const base = loaded as unknown as Record<string, unknown>;

  for (const key of Object.keys(mine)) {
    if (key === "extent" || key === "assets" || key === "stac_extensions") continue;
    if (!sameJson(mine[key], base[key])) setOrDelete(out, key, mine[key]);
  }

  out.extent = mergeExtent(loaded.extent, edited.extent, latest.extent);
  setOrDelete(out, "assets", mergeAssets(loaded.assets, edited.assets, latest.assets));
  setOrDelete(
    out,
    "stac_extensions",
    mergeExtensions(loaded.stac_extensions, edited.stac_extensions, latest.stac_extensions),
  );
  return out as unknown as StacCollection;
}

function mergeExtent(
  loaded: StacCollection["extent"] | undefined,
  edited: StacCollection["extent"],
  latest: StacCollection["extent"] | undefined,
): StacCollection["extent"] {
  if (!latest) return edited;
  const out = { ...latest };
  if (!sameJson(edited.spatial, loaded?.spatial)) out.spatial = edited.spatial;
  if (!sameJson(edited.temporal, loaded?.temporal)) out.temporal = edited.temporal;
  return out;
}

function mergeAssets(
  loaded: Record<string, StacAsset> | undefined,
  edited: Record<string, StacAsset> | undefined,
  latest: Record<string, StacAsset> | undefined,
): Record<string, StacAsset> | undefined {
  const before = loaded ?? {};
  const mine = edited ?? {};
  const stored = latest ?? {};
  const out: Record<string, StacAsset> = {};
  const keys = new Set([...Object.keys(stored), ...Object.keys(before), ...Object.keys(mine)]);
  for (const key of keys) {
    if (isPipelineAsset(stored[key]) || isPipelineAsset(before[key])) {
      // The pipeline's: the stored copy, or gone if the pipeline removed it.
      if (key in stored) out[key] = stored[key];
      continue;
    }
    const changed = !sameJson(mine[key], before[key]);
    if (!changed) {
      if (key in stored) out[key] = stored[key];
    } else if (key in mine) {
      // The form knows href/type/title/description/roles and strips the rest;
      // lay its fields over the stored asset so extension fields survive.
      const fromForm = mine[key] as unknown as Record<string, unknown>;
      const merged: Record<string, unknown> = { ...(stored[key] ?? {}), ...fromForm };
      for (const field of ["type", "title", "description", "roles"]) {
        if (fromForm[field] === undefined) delete merged[field];
      }
      out[key] = merged as unknown as StacAsset;
    }
    // changed and absent from the form: the user deleted it.
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function mergeExtensions(
  loaded: string[] | undefined,
  edited: string[] | undefined,
  latest: string[] | undefined,
): string[] | undefined {
  if (sameJson(edited, loaded)) return latest;
  const before = new Set(loaded ?? []);
  const out = [...(edited ?? [])];
  for (const url of latest ?? []) {
    if (!before.has(url) && !out.includes(url)) out.push(url);
  }
  return out.length > 0 ? out : undefined;
}

function isPipelineAsset(asset: unknown): boolean {
  return typeof asset === "object" && asset !== null && PIPELINE_ASSET_MARKER in asset;
}

function setOrDelete(target: Record<string, unknown>, key: string, value: unknown) {
  if (value === undefined) delete target[key];
  else target[key] = value;
}

/** Deep equality on the JSON the value serialises to (key order ignored). */
export function sameJson(a: unknown, b: unknown): boolean {
  return canonical(a) === canonical(b);
}

function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(Object.entries(v).sort(([x], [y]) => (x < y ? -1 : x > y ? 1 : 0)))
      : v,
  ) ?? "undefined";
}
