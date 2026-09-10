import { describe, it, expect, vi, beforeAll } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { AxisTick } from "@/lib/map/axis";
import { TimeBar } from "@/components/map/TimeBar";

beforeAll(() => {
  // Radix Select needs these in jsdom (the house pattern — see
  // `ingest-form-window.test.tsx`).
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn() as never;
});

const AXIS: AxisTick[] = [
  { instant: Date.parse("2026-09-04T06:00:00Z"), label: "2026-09-04 06:00 UTC" },
  { instant: Date.parse("2026-09-04T06:05:00Z"), label: "2026-09-04 06:05 UTC" },
  { instant: Date.parse("2026-09-04T06:10:00Z"), label: "2026-09-04 06:10 UTC" },
];

function renderBar(axis = AXIS, index = axis.length - 1) {
  const onIndexChange = vi.fn();
  const onFrameSpanChange = vi.fn();
  render(
    <TimeBar
      axis={axis}
      index={index}
      onIndexChange={onIndexChange}
      frameSpan={50}
      onFrameSpanChange={onFrameSpanChange}
      canAdvance={() => true}
    />,
  );
  return { onIndexChange, onFrameSpanChange };
}

describe("TimeBar", () => {
  it("reads out the tick it is parked on, over the whole axis", () => {
    renderBar();

    expect(screen.getByText("2026-09-04 06:10 UTC")).toBeTruthy();
    expect(screen.getByText("3 / 3")).toBeTruthy();
  });

  it("reports a step back as an axis index", () => {
    const { onIndexChange } = renderBar();

    fireEvent.click(screen.getByLabelText("Previous frame"));

    expect(onIndexChange).toHaveBeenCalledWith(1);
  });

  it("renders nothing when no layer is time-aware", () => {
    const { container } = render(
      <TimeBar
        axis={[]}
        index={-1}
        onIndexChange={vi.fn()}
        frameSpan={50}
        onFrameSpanChange={vi.fn()}
        canAdvance={() => true}
      />,
    );

    expect(container.textContent).toBe("");
  });

  it("offers the four spans", () => {
    renderBar();

    expect(screen.getByRole("combobox", { name: "Frame span" })).toBeTruthy();
    expect(screen.getByText("50 frames")).toBeTruthy();
  });

  it("reports a span change", () => {
    const { onFrameSpanChange } = renderBar();

    fireEvent.click(screen.getByRole("combobox", { name: "Frame span" }));
    fireEvent.click(screen.getByRole("option", { name: "100 frames" }));

    expect(onFrameSpanChange).toHaveBeenCalledWith(100);
  });
});
