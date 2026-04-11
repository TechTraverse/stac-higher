import type { LayerSpecification } from "maplibre-gl";

export const FOOTPRINT_SOURCE = "stac-footprints";
export const EXTENT_SOURCE = "stac-extent";

export const footprintFillLayer: LayerSpecification = {
  id: "stac-footprint-fill",
  type: "fill",
  source: FOOTPRINT_SOURCE,
  paint: {
    "fill-color": "#3b82f6",
    "fill-opacity": [
      "case",
      ["boolean", ["feature-state", "hover"], false],
      0.25,
      0.1,
    ],
  },
};

export const footprintLineLayer: LayerSpecification = {
  id: "stac-footprint-line",
  type: "line",
  source: FOOTPRINT_SOURCE,
  paint: {
    "line-color": "#3b82f6",
    "line-width": [
      "case",
      ["boolean", ["feature-state", "hover"], false],
      3,
      1.5,
    ],
  },
};

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
