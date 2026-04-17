import { useRef, useCallback } from "react";
import { useStore } from "@nanostores/react";
import { $theme } from "@shared/stores/uiStore";
import Map, { NavigationControl, ScaleControl } from "react-map-gl/maplibre";
import type { MapRef, MapMouseEvent } from "react-map-gl/maplibre";
import type { LngLatBoundsLike } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

const BASEMAP_DARK =
  "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const BASEMAP_LIGHT =
  "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

interface StacMapProps {
  children?: React.ReactNode;
  className?: string;
  initialBounds?: LngLatBoundsLike;
  onMapRef?: (ref: MapRef) => void;
  onClick?: (e: MapMouseEvent) => void;
}

export function StacMap({
  children,
  className = "h-[400px] w-full rounded-lg overflow-hidden",
  initialBounds,
  onMapRef,
  onClick,
}: StacMapProps) {
  const theme = useStore($theme);
  const mapRef = useRef<MapRef>(null);

  const onLoad = useCallback(() => {
    if (mapRef.current && initialBounds) {
      mapRef.current.fitBounds(initialBounds, { padding: 50, duration: 0 });
    }
    if (mapRef.current && onMapRef) {
      onMapRef(mapRef.current);
    }
  }, [initialBounds, onMapRef]);

  return (
    <Map
      ref={mapRef}
      initialViewState={{
        longitude: 0,
        latitude: 20,
        zoom: 1.5,
      }}
      style={{ width: "100%", height: "100%" }}
      mapStyle={theme === "dark" ? BASEMAP_DARK : BASEMAP_LIGHT}
      onLoad={onLoad}
      onClick={onClick}
      attributionControl={false}
    >
      <NavigationControl position="top-right" />
      <ScaleControl position="bottom-left" />
      {children}
    </Map>
  );
}
