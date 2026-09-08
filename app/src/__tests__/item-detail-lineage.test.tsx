/**
 * The item page's "Derived from" block (D-2): upstream lineage rendered from
 * the item's own `derived_from` links, resolved by whatever the page hands in.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { StacItem, StacLink } from "@/lib/stac-api/types";

// maplibre needs WebGL; the geometry tab is never opened here but the module
// graph still imports the binding.
vi.mock("react-map-gl/maplibre", () => ({
  default: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Source: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Layer: () => <div />,
  NavigationControl: () => <div />,
  ScaleControl: () => <div />,
}));

import { ItemDetailView } from "@/components/items/ItemDetailView";

function makeItem(links: StacLink[]): StacItem {
  return {
    type: "Feature",
    stac_version: "1.0.0",
    id: "mask-1",
    collection: "cloud-masks",
    geometry: null,
    properties: { datetime: "2026-09-01T00:00:00Z" },
    links,
    assets: {},
  };
}

describe("item page lineage block", () => {
  it("renders nothing when the item carries no derived_from link", () => {
    render(
      <ItemDetailView
        item={makeItem([{ rel: "license", href: "https://example.com/license" }])}
      />,
    );
    expect(screen.queryByTestId("derived-from")).not.toBeInTheDocument();
  });

  it("renders one chip per link, linking where the resolver says", () => {
    const resolveLink = vi.fn((link: StacLink) => ({
      href: `/resolved${link.href}`,
      label: `L:${link.href}`,
      external: false,
    }));
    render(
      <ItemDetailView
        item={makeItem([
          { rel: "derived_from", href: "/collections/goes/items/s1" },
          { rel: "derived_from", href: "/collections/goes/items/s2" },
          { rel: "self", href: "/collections/cloud-masks/items/mask-1" },
        ])}
        resolveLink={resolveLink}
      />,
    );
    const block = screen.getByTestId("derived-from");
    expect(block).toHaveTextContent("Derived from");
    const anchors = block.querySelectorAll("a");
    expect(Array.from(anchors).map((a) => a.getAttribute("href"))).toEqual([
      "/resolved/collections/goes/items/s1",
      "/resolved/collections/goes/items/s2",
    ]);
    expect(anchors[0]).toHaveTextContent("L:/collections/goes/items/s1");
    expect(anchors[0]).not.toHaveAttribute("target");
    expect(resolveLink).toHaveBeenCalledTimes(2);
  });

  it("opens an unresolved href as an external anchor", () => {
    render(
      <ItemDetailView
        item={makeItem([
          { rel: "derived_from", href: "https://elsewhere.example/collections/c/items/i" },
        ])}
        resolveLink={() => ({
          href: "https://elsewhere.example/collections/c/items/i",
          label: "c / i",
          external: true,
        })}
      />,
    );
    const anchor = screen.getByTestId("derived-from").querySelector("a");
    expect(anchor).toHaveAttribute("href", "https://elsewhere.example/collections/c/items/i");
    expect(anchor).toHaveAttribute("target", "_blank");
    expect(anchor).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("uses the product routes by default, with no catalog context", () => {
    render(
      <ItemDetailView
        item={makeItem([{ rel: "derived_from", href: "/collections/goes/items/s1" }])}
      />,
    );
    const anchor = screen.getByTestId("derived-from").querySelector("a");
    expect(anchor).toHaveAttribute("href", "/collections/goes/items/s1");
    expect(anchor).toHaveTextContent("goes / s1");
  });
});
