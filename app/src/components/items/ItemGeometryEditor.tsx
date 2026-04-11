import { useState, useCallback, useRef } from "react";
import Map, { Source, Layer, NavigationControl } from "react-map-gl/maplibre";
import type { MapRef, MapMouseEvent } from "react-map-gl/maplibre";
import type { DrawMode } from "@/stores/mapStore";
import { DrawingToolbar } from "@/components/map/DrawingToolbar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Trash2 } from "lucide-react";
import "maplibre-gl/dist/maplibre-gl.css";

const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

interface ItemGeometryEditorProps {
  value: GeoJSON.Geometry | null;
  onChange: (geometry: GeoJSON.Geometry | null) => void;
}

export function ItemGeometryEditor({ value, onChange }: ItemGeometryEditorProps) {
  const mapRef = useRef<MapRef>(null);
  const [drawMode, setDrawMode] = useState<DrawMode>(null);
  const [points, setPoints] = useState<[number, number][]>([]);
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState("");
  const [bboxStart, setBboxStart] = useState<[number, number] | null>(null);

  const geojsonData: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: value
      ? [{ type: "Feature", properties: {}, geometry: value }]
      : [],
  };

  const drawPreview: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features:
      points.length > 0
        ? [
            {
              type: "Feature",
              properties: {},
              geometry:
                points.length === 1
                  ? { type: "Point", coordinates: points[0] }
                  : points.length === 2
                    ? { type: "LineString", coordinates: points }
                    : {
                        type: "Polygon",
                        coordinates: [[...points, points[0]]],
                      },
            },
          ]
        : [],
  };

  const handleMapClick = useCallback(
    (e: MapMouseEvent) => {
      const lngLat: [number, number] = [e.lngLat.lng, e.lngLat.lat];

      if (drawMode === "point") {
        onChange({ type: "Point", coordinates: lngLat });
        setDrawMode(null);
        return;
      }

      if (drawMode === "bbox") {
        if (!bboxStart) {
          setBboxStart(lngLat);
        } else {
          const [minLng, maxLng] =
            bboxStart[0] < lngLat[0]
              ? [bboxStart[0], lngLat[0]]
              : [lngLat[0], bboxStart[0]];
          const [minLat, maxLat] =
            bboxStart[1] < lngLat[1]
              ? [bboxStart[1], lngLat[1]]
              : [lngLat[1], bboxStart[1]];

          onChange({
            type: "Polygon",
            coordinates: [
              [
                [minLng, minLat],
                [maxLng, minLat],
                [maxLng, maxLat],
                [minLng, maxLat],
                [minLng, minLat],
              ],
            ],
          });
          setBboxStart(null);
          setDrawMode(null);
        }
        return;
      }

      if (drawMode === "polygon") {
        setPoints((prev) => [...prev, lngLat]);
      }
    },
    [drawMode, bboxStart, onChange],
  );

  const handleMapDblClick = useCallback(
    (e: MapMouseEvent) => {
      if (drawMode === "polygon" && points.length >= 3) {
        e.preventDefault();
        const closed = [...points, points[0]];
        onChange({
          type: "Polygon",
          coordinates: [closed],
        });
        setPoints([]);
        setDrawMode(null);
      }
    },
    [drawMode, points, onChange],
  );

  const handleModeChange = (mode: DrawMode) => {
    setDrawMode(mode);
    setPoints([]);
    setBboxStart(null);
  };

  const handleJsonApply = () => {
    try {
      const parsed = JSON.parse(jsonText);
      if (parsed.type && parsed.coordinates) {
        onChange(parsed);
        setJsonMode(false);
      }
    } catch {
      // invalid JSON, do nothing
    }
  };

  const cursorStyle =
    drawMode === "point"
      ? "crosshair"
      : drawMode === "polygon" || drawMode === "bbox"
        ? "crosshair"
        : "grab";

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <Label>Geometry</Label>
        <div className="flex items-center gap-2">
          {value && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => onChange(null)}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" />
              Clear
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setJsonMode(!jsonMode);
              if (!jsonMode && value) {
                setJsonText(JSON.stringify(value, null, 2));
              }
            }}
          >
            {jsonMode ? "Map" : "JSON"}
          </Button>
        </div>
      </div>

      {jsonMode ? (
        <div className="space-y-2">
          <Textarea
            rows={10}
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
            className="font-mono text-xs"
            placeholder='{"type": "Polygon", "coordinates": [...]}'
          />
          <Button type="button" size="sm" onClick={handleJsonApply}>
            Apply GeoJSON
          </Button>
        </div>
      ) : (
        <div className="relative h-[400px] rounded-lg overflow-hidden border border-border">
          <DrawingToolbar activeMode={drawMode} onModeChange={handleModeChange} />
          {drawMode && (
            <div className="absolute bottom-3 left-3 z-10 bg-background/90 backdrop-blur rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground">
              {drawMode === "point" && "Click to place a point"}
              {drawMode === "polygon" &&
                (points.length < 3
                  ? `Click to add vertices (${points.length}/3 min)`
                  : "Double-click to finish polygon")}
              {drawMode === "bbox" &&
                (!bboxStart
                  ? "Click first corner of rectangle"
                  : "Click opposite corner")}
            </div>
          )}
          <Map
            ref={mapRef}
            initialViewState={{ longitude: 0, latitude: 20, zoom: 1.5 }}
            style={{ width: "100%", height: "100%" }}
            mapStyle={BASEMAP}
            onClick={handleMapClick}
            onDblClick={handleMapDblClick}
            doubleClickZoom={!drawMode}
            cursor={cursorStyle}
            attributionControl={false}
          >
            <NavigationControl position="top-right" />
            <Source id="geometry" type="geojson" data={geojsonData}>
              <Layer
                id="geometry-fill"
                type="fill"
                paint={{ "fill-color": "#3b82f6", "fill-opacity": 0.15 }}
              />
              <Layer
                id="geometry-line"
                type="line"
                paint={{ "line-color": "#3b82f6", "line-width": 2 }}
              />
              <Layer
                id="geometry-point"
                type="circle"
                filter={["==", "$type", "Point"]}
                paint={{
                  "circle-color": "#3b82f6",
                  "circle-radius": 6,
                  "circle-stroke-color": "#fff",
                  "circle-stroke-width": 2,
                }}
              />
            </Source>
            <Source id="draw-preview" type="geojson" data={drawPreview}>
              <Layer
                id="draw-preview-fill"
                type="fill"
                paint={{ "fill-color": "#f59e0b", "fill-opacity": 0.1 }}
              />
              <Layer
                id="draw-preview-line"
                type="line"
                paint={{
                  "line-color": "#f59e0b",
                  "line-width": 2,
                  "line-dasharray": [3, 2],
                }}
              />
              <Layer
                id="draw-preview-points"
                type="circle"
                filter={["==", "$type", "Point"]}
                paint={{
                  "circle-color": "#f59e0b",
                  "circle-radius": 5,
                  "circle-stroke-color": "#fff",
                  "circle-stroke-width": 2,
                }}
              />
            </Source>
          </Map>
        </div>
      )}

      {value && (
        <p className="text-xs text-muted-foreground">
          Geometry type: <span className="font-mono">{value.type}</span>
        </p>
      )}
    </div>
  );
}
