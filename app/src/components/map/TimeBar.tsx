/**
 * The /map page's docked time bar (spec §4.4): one axis over every time-aware
 * layer, plus the page-wide frame span.
 *
 * It shows only while an axis exists — a page of vector layers, or of products
 * whose items carry no timestamp, has nothing to scrub.
 */
import { useMemo } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  TimeSlider,
} from "@stac-higher/shared";
import type { AxisTick } from "@/lib/map/axis";
import { FRAME_SPANS, type FrameSpan } from "@/lib/map/state";
import { FRAME_MAX_WAIT_TICKS } from "@/lib/serving/frames";

export interface TimeBarProps {
  axis: AxisTick[];
  index: number;
  onIndexChange: (index: number) => void;
  frameSpan: FrameSpan;
  onFrameSpanChange: (span: FrameSpan) => void;
  /**
   * Whether playback may advance — the preview's rule: the map has finished
   * the tiles of every mounted source, so the incoming frame is complete.
   */
  canAdvance: () => boolean;
}

export function TimeBar({
  axis,
  index,
  onIndexChange,
  frameSpan,
  onFrameSpanChange,
  canAdvance,
}: TimeBarProps) {
  const labels = useMemo(() => axis.map((tick) => tick.label), [axis]);
  if (axis.length === 0) return null;

  return (
    <div className="border-t border-border bg-background px-3 py-2" data-testid="map-time-bar">
      <TimeSlider
        labels={labels}
        index={index}
        onIndexChange={onIndexChange}
        canAdvance={canAdvance}
        maxWaitTicks={FRAME_MAX_WAIT_TICKS}
      >
        <Select
          value={String(frameSpan)}
          onValueChange={(value) => onFrameSpanChange(Number(value) as FrameSpan)}
        >
          <SelectTrigger className="w-[7.5rem]" aria-label="Frame span">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FRAME_SPANS.map((span) => (
              <SelectItem key={span} value={String(span)}>
                {span} frames
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TimeSlider>
    </div>
  );
}
