import { useEffect, useRef, type ReactNode } from "react";
import { useState } from "react";
import { ChevronLeft, ChevronRight, Pause, Play } from "lucide-react";
import { Button } from "@shared/components/ui/button";
import { Slider } from "@shared/components/ui/slider";

export interface TimeSliderProps {
  /** One label per frame, oldest first — the length defines the axis. */
  labels: string[];
  /** The frame currently shown. Owned by the caller, which also clamps it. */
  index: number;
  onIndexChange: (index: number) => void;
  /** Playback rate, when frames are ready. */
  fps?: number;
  /**
   * Whether the next frame can be shown yet. Playback waits on this rather
   * than dropping frames, because the frames are tiles: the first pass runs
   * at whatever rate the tile server can render, replays run at `fps`.
   */
  canAdvance?: (nextIndex: number) => boolean;
  /**
   * Ticks to wait on `canAdvance` before advancing anyway. Without a cap, one
   * frame the tile server never finishes would stop playback for good.
   */
  maxWaitTicks?: number;
  /** Extra controls rendered at the trailing edge (pickers, links). */
  children?: ReactNode;
}

/**
 * Play / scrub controls for a series of frames.
 *
 * Deliberately knows nothing about maps or time: it is handed labels and an
 * index, and reports the index the user asked for. Playback state is the one
 * thing it owns, because nothing outside it needs to read it.
 */
export function TimeSlider({
  labels,
  index,
  onIndexChange,
  fps = 4,
  canAdvance,
  maxWaitTicks = 12,
  children,
}: TimeSliderProps) {
  const [playing, setPlaying] = useState(false);
  const count = labels.length;
  const playable = count > 1;

  // The interval reads the index through a ref: re-creating it on every frame
  // would restart the timer each tick and make playback drift.
  const indexRef = useRef(index);
  indexRef.current = index;
  const onChangeRef = useRef(onIndexChange);
  onChangeRef.current = onIndexChange;
  const canAdvanceRef = useRef(canAdvance);
  canAdvanceRef.current = canAdvance;
  const waitedRef = useRef(0);

  useEffect(() => {
    if (!playing || !playable) return;
    waitedRef.current = 0;
    const timer = setInterval(() => {
      const next = (indexRef.current + 1) % count;
      const ready = canAdvanceRef.current?.(next) ?? true;
      if (!ready && waitedRef.current < maxWaitTicks) {
        waitedRef.current += 1;
        return;
      }
      waitedRef.current = 0;
      onChangeRef.current(next);
    }, 1000 / fps);
    return () => clearInterval(timer);
  }, [playing, playable, count, fps, maxWaitTicks]);

  useEffect(() => {
    if (!playable) setPlaying(false);
  }, [playable]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="icon"
        aria-label={playing ? "Pause" : "Play"}
        disabled={!playable}
        onClick={() => setPlaying((p) => !p)}
      >
        {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </Button>
      <Button
        variant="outline"
        size="icon"
        aria-label="Previous frame"
        disabled={index <= 0}
        onClick={() => onIndexChange(index - 1)}
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <Button
        variant="outline"
        size="icon"
        aria-label="Next frame"
        disabled={index >= count - 1}
        onClick={() => onIndexChange(index + 1)}
      >
        <ChevronRight className="h-4 w-4" />
      </Button>

      <Slider
        className="min-w-[8rem] flex-1"
        aria-label="Frame"
        value={[index]}
        min={0}
        max={Math.max(count - 1, 1)}
        step={1}
        disabled={!playable}
        onValueChange={([value]) => onIndexChange(value)}
      />

      <span className="font-mono text-xs tabular-nums">{labels[index] ?? ""}</span>
      <span className="text-xs text-muted-foreground tabular-nums">
        {count > 0 ? `${index + 1} / ${count}` : "0 / 0"}
      </span>
      {children}
    </div>
  );
}
