import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { TimeSlider } from "@stac-higher/shared";

const LABELS = ["06:01 UTC", "06:06 UTC", "06:11 UTC"];

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function renderSlider(props: Partial<React.ComponentProps<typeof TimeSlider>> = {}) {
  const onIndexChange = vi.fn();
  const view = render(
    <TimeSlider labels={LABELS} index={0} onIndexChange={onIndexChange} {...props} />,
  );
  return { onIndexChange, view };
}

describe("TimeSlider", () => {
  it("reads out the current frame's label and position", () => {
    renderSlider({ index: 1 });

    expect(screen.getByText("06:06 UTC")).toBeTruthy();
    expect(screen.getByText("2 / 3")).toBeTruthy();
  });

  it("steps one frame at a time and stops at the ends", () => {
    const { onIndexChange, view } = renderSlider({ index: 0 });

    expect(screen.getByLabelText("Previous frame").hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByLabelText("Next frame"));
    expect(onIndexChange).toHaveBeenCalledWith(1);

    view.rerender(
      <TimeSlider labels={LABELS} index={2} onIndexChange={onIndexChange} />,
    );
    expect(screen.getByLabelText("Next frame").hasAttribute("disabled")).toBe(true);
  });

  it("advances at the given rate while playing, and loops", () => {
    const onIndexChange = vi.fn();
    const view = render(
      <TimeSlider labels={LABELS} index={0} onIndexChange={onIndexChange} fps={4} />,
    );

    fireEvent.click(screen.getByLabelText("Play"));
    act(() => void vi.advanceTimersByTime(250));
    expect(onIndexChange).toHaveBeenLastCalledWith(1);

    // The parent owns the index, so replay the advance it would have made.
    view.rerender(
      <TimeSlider labels={LABELS} index={2} onIndexChange={onIndexChange} fps={4} />,
    );
    act(() => void vi.advanceTimersByTime(250));
    expect(onIndexChange).toHaveBeenLastCalledWith(0);
  });

  it("stops advancing once paused", () => {
    const { onIndexChange } = renderSlider({ fps: 4 });

    fireEvent.click(screen.getByLabelText("Play"));
    fireEvent.click(screen.getByLabelText("Pause"));
    act(() => void vi.advanceTimersByTime(2000));

    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("waits for the next frame instead of dropping it", () => {
    // Frames are tiles: the first pass runs at the tile server's pace.
    const onIndexChange = vi.fn();
    let ready = false;
    render(
      <TimeSlider
        labels={LABELS}
        index={0}
        onIndexChange={onIndexChange}
        fps={4}
        canAdvance={() => ready}
      />,
    );

    fireEvent.click(screen.getByLabelText("Play"));
    act(() => void vi.advanceTimersByTime(1000));
    expect(onIndexChange).not.toHaveBeenCalled();

    ready = true;
    act(() => void vi.advanceTimersByTime(250));
    expect(onIndexChange).toHaveBeenCalledWith(1);
  });

  it("gives up waiting rather than stalling on a frame that never loads", () => {
    const onIndexChange = vi.fn();
    render(
      <TimeSlider
        labels={LABELS}
        index={0}
        onIndexChange={onIndexChange}
        fps={4}
        canAdvance={() => false}
        maxWaitTicks={2}
      />,
    );

    fireEvent.click(screen.getByLabelText("Play"));
    act(() => void vi.advanceTimersByTime(750));

    expect(onIndexChange).toHaveBeenCalledWith(1);
  });

  it("has nothing to play with a single frame", () => {
    renderSlider({ labels: ["06:01 UTC"], index: 0 });

    expect(screen.getByLabelText("Play").hasAttribute("disabled")).toBe(true);
  });
});
