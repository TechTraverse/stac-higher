/**
 * `PipelineDag` node chips say what TYPE each node is with an icon, not only
 * with the 2px left bar.
 *
 * The regression this guards is the GOES demo's `goes-geocolor` pair: a
 * process and the product it writes share a label, and a thin orange bar
 * beside a red health dot was the only thing telling them apart. Both the
 * picture (the icon) and the accessible name must carry the type.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FIXTURE_GRAPH, lineage, PipelineDag } from "@stac-higher/shared";

function renderRow() {
  render(
    <PipelineDag
      graph={lineage(FIXTURE_GRAPH, "coll:goes-geocolor")}
      decorate={(node) => ({ href: `/node/${node.id}` })}
      label="goes-geocolor lineage"
    />,
  );
}

describe("PipelineDag node types", () => {
  it("tells a process from the same-named product it writes", () => {
    renderRow();
    expect(
      screen.getByRole("link", { name: "Process: goes-geocolor" }),
    ).toHaveAttribute("href", "/node/proc:goes-geocolor");
    expect(
      screen.getByRole("link", { name: "Product: goes-geocolor" }),
    ).toHaveAttribute("href", "/node/coll:goes-geocolor");
  });

  it("draws the type icon on every chip, matching the sidebar's icons", () => {
    renderRow();
    const icon = { connection: "lucide-plug", collection: "lucide-layers", process: "lucide-cpu" };
    const chips = document.querySelectorAll<HTMLElement>("[data-node-kind]");
    expect(chips.length).toBeGreaterThan(0);
    for (const chip of chips) {
      const kind = chip.dataset.nodeKind as keyof typeof icon;
      const svg = chip.querySelector("svg");
      expect(svg, `${chip.textContent} has an icon`).not.toBeNull();
      expect(svg).toHaveClass(icon[kind]);
      // Decoration only: the type is spoken through the chip's name instead.
      expect(svg).toHaveAttribute("aria-hidden", "true");
    }
  });

  it("keeps the extractor badge a separate word in the name", () => {
    renderRow();
    expect(
      screen.getByRole("link", { name: "Process: goes-abi-metadata extractor" }),
    ).toBeInTheDocument();
  });

  it("names the type in the hover title too", () => {
    renderRow();
    expect(
      screen.getByRole("link", { name: "Process: goes-geocolor" }),
    ).toHaveAttribute("title", "goes-geocolor (process)");
  });
});
