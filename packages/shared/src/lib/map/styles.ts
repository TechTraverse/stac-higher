import type { ExpressionSpecification, LayerSpecification } from "maplibre-gl";

export const FOOTPRINT_SOURCE = "stac-footprints";
export const EXTENT_SOURCE = "stac-extent";
/** Raster preview overlay (G-5): tiles served by titiler-pgstac. */
export const RASTER_PREVIEW_SOURCE = "stac-raster-preview";
export const RASTER_PREVIEW_LAYER = "stac-raster-preview-layer";

/** The hover-aware fill opacity every footprint layer uses. */
const FOOTPRINT_FILL_OPACITY: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "hover"], false],
  0.25,
  0.1,
];

/**
 * Layer ids for a footprint source. The default source keeps the ids the
 * single-layer pages have always used; any other source is namespaced so
 * several footprint layers can share one map (the /map page).
 */
export function footprintLayerIds(sourceId: string): { fill: string; line: string } {
  if (sourceId === FOOTPRINT_SOURCE) {
    return { fill: "stac-footprint-fill", line: "stac-footprint-line" };
  }
  return { fill: `${sourceId}-fill`, line: `${sourceId}-line` };
}

/**
 * Fill + line specs for a footprint source. `opacity` (0..1) scales the whole
 * layer — the hover contrast is preserved inside it — and is omitted from
 * the spec entirely at 1 so the default output is unchanged.
 */
export function footprintLayers(
  sourceId: string,
  opacity = 1,
): { fill: LayerSpecification; line: LayerSpecification } {
  const ids = footprintLayerIds(sourceId);
  const full = opacity === 1;
  return {
    fill: {
      id: ids.fill,
      type: "fill",
      source: sourceId,
      paint: {
        "fill-color": "#3b82f6",
        "fill-opacity": full
          ? FOOTPRINT_FILL_OPACITY
          : (["*", opacity, FOOTPRINT_FILL_OPACITY] as ExpressionSpecification),
      },
    },
    line: {
      id: ids.line,
      type: "line",
      source: sourceId,
      paint: {
        "line-color": "#3b82f6",
        "line-width": [
          "case",
          ["boolean", ["feature-state", "hover"], false],
          3,
          1.5,
        ],
        ...(full ? {} : { "line-opacity": opacity }),
      },
    },
  };
}

export const footprintFillLayer = footprintLayers(FOOTPRINT_SOURCE).fill as Extract<
  LayerSpecification,
  { type: "fill" }
>;
export const footprintLineLayer = footprintLayers(FOOTPRINT_SOURCE).line as Extract<
  LayerSpecification,
  { type: "line" }
>;

export const extentFillLayer: LayerSpecification = {
  id: "stac-extent-fill",
  type: "fill",
  source: EXTENT_SOURCE,
  paint: {
    "fill-color": "#f59e0b",
    "fill-opacity": 0.08,
  },
};

export const extentLineLayer: LayerSpecification = {
  id: "stac-extent-line",
  type: "line",
  source: EXTENT_SOURCE,
  paint: {
    "line-color": "#f59e0b",
    "line-width": 2,
    "line-dasharray": [3, 2],
  },
};

export const selectedFillLayer: LayerSpecification = {
  id: "stac-selected-fill",
  type: "fill",
  source: FOOTPRINT_SOURCE,
  paint: {
    "fill-color": "#f59e0b",
    "fill-opacity": 0.2,
  },
  filter: ["==", ["get", "selected"], true],
};

export const selectedLineLayer: LayerSpecification = {
  id: "stac-selected-line",
  type: "line",
  source: FOOTPRINT_SOURCE,
  paint: {
    "line-color": "#f59e0b",
    "line-width": 3,
  },
  filter: ["==", ["get", "selected"], true],
};
