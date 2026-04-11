import { describe, it, expect } from "vitest";
import {
  bboxToPolygon,
  bboxToLngLatBounds,
  geometryToBbox,
} from "@/lib/map/bbox";

describe("bboxToPolygon", () => {
  it("converts a bbox to a GeoJSON Polygon feature", () => {
    const result = bboxToPolygon([-105, 40, -104, 41]);
    expect(result.type).toBe("Feature");
    expect(result.geometry.type).toBe("Polygon");
    expect(result.geometry.coordinates).toEqual([
      [
        [-105, 40],
        [-104, 40],
        [-104, 41],
        [-105, 41],
        [-105, 40],
      ],
    ]);
  });

  it("produces a closed ring", () => {
    const result = bboxToPolygon([0, 0, 10, 10]);
    const ring = result.geometry.coordinates[0];
    expect(ring[0]).toEqual(ring[ring.length - 1]);
  });

  it("handles global extent", () => {
    const result = bboxToPolygon([-180, -90, 180, 90]);
    expect(result.geometry.coordinates[0]).toHaveLength(5);
  });

  it("handles zero-area bbox (point)", () => {
    const result = bboxToPolygon([10, 20, 10, 20]);
    expect(result.geometry.type).toBe("Polygon");
    const ring = result.geometry.coordinates[0];
    expect(ring[0]).toEqual([10, 20]);
  });
});

describe("bboxToLngLatBounds", () => {
  it("converts bbox to LngLatBounds format", () => {
    const result = bboxToLngLatBounds([-105, 40, -104, 41]);
    expect(result).toEqual([
      [-105, 40],
      [-104, 41],
    ]);
  });

  it("handles global extent", () => {
    const result = bboxToLngLatBounds([-180, -90, 180, 90]);
    expect(result).toEqual([
      [-180, -90],
      [180, 90],
    ]);
  });
});

describe("geometryToBbox", () => {
  it("computes bbox for a Point", () => {
    const result = geometryToBbox({
      type: "Point",
      coordinates: [-105.0, 40.0],
    });
    expect(result).toEqual([-105, 40, -105, 40]);
  });

  it("computes bbox for a Polygon", () => {
    const result = geometryToBbox({
      type: "Polygon",
      coordinates: [
        [
          [-105, 40],
          [-104, 40],
          [-104, 41],
          [-105, 41],
          [-105, 40],
        ],
      ],
    });
    expect(result).toEqual([-105, 40, -104, 41]);
  });

  it("computes bbox for a MultiPolygon", () => {
    const result = geometryToBbox({
      type: "MultiPolygon",
      coordinates: [
        [
          [
            [0, 0],
            [1, 0],
            [1, 1],
            [0, 1],
            [0, 0],
          ],
        ],
        [
          [
            [10, 10],
            [11, 10],
            [11, 11],
            [10, 11],
            [10, 10],
          ],
        ],
      ],
    });
    expect(result).toEqual([0, 0, 11, 11]);
  });

  it("computes bbox for a LineString", () => {
    const result = geometryToBbox({
      type: "LineString",
      coordinates: [
        [0, 0],
        [5, 5],
        [10, 2],
      ],
    });
    expect(result).toEqual([0, 0, 10, 5]);
  });

  it("computes bbox for a MultiPoint", () => {
    const result = geometryToBbox({
      type: "MultiPoint",
      coordinates: [
        [-10, -20],
        [30, 40],
      ],
    });
    expect(result).toEqual([-10, -20, 30, 40]);
  });

  it("computes bbox for a MultiLineString", () => {
    const result = geometryToBbox({
      type: "MultiLineString",
      coordinates: [
        [
          [0, 0],
          [1, 1],
        ],
        [
          [5, 5],
          [10, 10],
        ],
      ],
    });
    expect(result).toEqual([0, 0, 10, 10]);
  });

  it("computes bbox for a GeometryCollection", () => {
    const result = geometryToBbox({
      type: "GeometryCollection",
      geometries: [
        { type: "Point", coordinates: [-50, -30] },
        { type: "Point", coordinates: [50, 30] },
      ],
    });
    expect(result).toEqual([-50, -30, 50, 30]);
  });

  it("handles nested GeometryCollection", () => {
    const result = geometryToBbox({
      type: "GeometryCollection",
      geometries: [
        {
          type: "GeometryCollection",
          geometries: [
            { type: "Point", coordinates: [0, 0] },
            { type: "Point", coordinates: [10, 10] },
          ],
        },
        { type: "Point", coordinates: [-5, -5] },
      ],
    });
    expect(result).toEqual([-5, -5, 10, 10]);
  });

  it("returns null for empty GeometryCollection", () => {
    const result = geometryToBbox({
      type: "GeometryCollection",
      geometries: [],
    });
    expect(result).toBeNull();
  });
});
