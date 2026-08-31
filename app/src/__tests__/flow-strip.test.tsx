/**
 * FlowStrip (M5-F): the two decisions that make the strip honest — a missing
 * day is not a quiet day, and failure beats volume.
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { FlowStrip } from "@/components/monitoring/FlowStrip";
import type { DailyStats } from "@/lib/monitoring/graph-api";

function day(offset: number, overrides: Partial<DailyStats> = {}): DailyStats {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - offset);
  return {
    day: date.toISOString().slice(0, 10),
    files: 0,
    items: 0,
    bytes: 0,
    delivered: 0,
    failed: 0,
    dead: 0,
    runs: 0,
    ...overrides,
  };
}

describe("FlowStrip", () => {
  it("draws one cell per calendar day, not per returned row", () => {
    const { container } = render(<FlowStrip days={[day(1, { items: 5 })]} window={7} />);
    expect(container.querySelectorAll("rect")).toHaveLength(7);
  });

  it("distinguishes a MISSING day from a quiet one", () => {
    // A gap means the rollup did not run; a quiet day means it ran and
    // nothing happened. Collapsing the two would hide a broken job.
    const { container } = render(<FlowStrip days={[day(1, { items: 0 })]} window={3} />);
    const rects = Array.from(container.querySelectorAll("rect"));
    const titles = rects.map((r) => r.querySelector("title")?.textContent ?? "");
    expect(titles.filter((t) => t.includes("no data"))).toHaveLength(2);
    expect(titles.filter((t) => t.includes("0 items"))).toHaveLength(1);
  });

  it("colours a day with failures as failed even when it moved volume", () => {
    const { container } = render(
      <FlowStrip days={[day(1, { items: 1000, failed: 1 })]} window={1} />,
    );
    const rect = container.querySelector("rect");
    expect(rect?.getAttribute("class")).toContain("fill-destructive");
  });

  it("colours a busy healthy day as healthy", () => {
    const { container } = render(
      <FlowStrip days={[day(1, { items: 1000 })]} window={1} />,
    );
    expect(container.querySelector("rect")?.getAttribute("class")).toContain(
      "fill-primary",
    );
  });

  it("ends at yesterday — today's bucket is not written yet", () => {
    // Including today would show every flow as newly dead each morning,
    // because the rollup only covers complete days.
    const today = new Date().toISOString().slice(0, 10);
    const { container } = render(<FlowStrip days={[]} window={5} />);
    const titles = Array.from(container.querySelectorAll("title")).map(
      (t) => t.textContent ?? "",
    );
    expect(titles.some((t) => t.startsWith(today))).toBe(false);
  });

  it("is labelled for screen readers", () => {
    const { container } = render(<FlowStrip days={[]} window={30} label="run history" />);
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe(
      "30-day run history",
    );
  });
});
