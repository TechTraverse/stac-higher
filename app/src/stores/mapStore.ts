import { atom } from "nanostores";
import type GeoJSON from "geojson";

export interface MapViewState {
  longitude: number;
  latitude: number;
  zoom: number;
}

export const $mapViewState = atom<MapViewState>({
  longitude: 0,
  latitude: 0,
  zoom: 2,
});

export const $selectedFeatureIds = atom<string[]>([]);

export type DrawMode = "polygon" | "bbox" | "point" | null;

export const $drawMode = atom<DrawMode>(null);
export const $drawnGeometry = atom<GeoJSON.Geometry | null>(null);
