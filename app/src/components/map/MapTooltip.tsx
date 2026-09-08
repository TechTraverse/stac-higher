/**
 * The hover readout over a footprint (spec §4.6): item id and time, placed at
 * the pointer. Deliberately not a maplibre Popup — a Popup is anchored to a
 * coordinate and animates; this follows the cursor and must never intercept
 * the mouse events the map is listening for.
 */
interface MapTooltipProps {
  id: string;
  datetime: string;
  /** Pixel offset inside the map container, from the mouse event's `point`. */
  x: number;
  y: number;
}

export function MapTooltip({ id, datetime, x, y }: MapTooltipProps) {
  return (
    <div
      data-testid="map-tooltip"
      className="pointer-events-none absolute z-10 max-w-[16rem] rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md"
      style={{ left: x + 12, top: y + 12 }}
    >
      <div className="tech truncate font-medium">{id}</div>
      {datetime && <div className="text-muted-foreground">{datetime}</div>}
    </div>
  );
}
