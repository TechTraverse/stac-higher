import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TimeSeriesChart } from "@stac-higher/shared";

const T0 = Date.parse("2026-10-03T17:00:00Z");
const pts = (values: (number | null)[]) =>
  values.map((value, i) => ({ t: T0 + i * 300_000, value }));

function draw(values: (number | null)[], unit?: string) {
  return render(<TimeSeriesChart points={pts(values)} ariaLabel="CMI" unit={unit} />);
}

describe("TimeSeriesChart", () => {
  it("breaks the line at a null", () => {
    const { container } = draw([1, 2, null, 4, 5]);
    expect(container.querySelectorAll('[data-testid="series-segment"]')).toHaveLength(2);
    expect(container.querySelectorAll('[data-testid="series-point"]')).toHaveLength(0);
  });

  it("draws an isolated value as a point", () => {
    const { container } = draw([1, null, 3, 4]);
    expect(container.querySelectorAll('[data-testid="series-segment"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="series-point"]')).toHaveLength(1);
  });

  it("says so when every value is missing", () => {
    const { container } = draw([null, null]);
    expect(container.querySelectorAll("path")).toHaveLength(0);
    expect(screen.getByText("No values at this point")).toBeTruthy();
  });

  it("survives a flat series", () => {
    const { container } = draw([250, 250, 250]);
    const segs = container.querySelectorAll('[data-testid="series-segment"]');
    expect(segs).toHaveLength(1);
    expect(segs[0].getAttribute("d")).not.toContain("NaN");
  });

  it("is an image with min/max and first/last time labels", () => {
    draw([250, 252.5, 260], "K");
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("CMI");
    expect(screen.getByText("250.0 K")).toBeTruthy();
    expect(screen.getByText("260.0 K")).toBeTruthy();
    expect(screen.getByText("17:00 UTC")).toBeTruthy();
    expect(screen.getByText("17:10 UTC")).toBeTruthy();
  });
});
