import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// The real Map needs WebGL. This stand-in records the props it was handed so
// the test can assert what StacMap forwards.
const { mapProps } = vi.hoisted(() => ({
  mapProps: { current: null as Record<string, unknown> | null },
}));

vi.mock("react-map-gl/maplibre", () => ({
  default: (props: Record<string, unknown>) => {
    mapProps.current = props;
    return <div data-testid="map">{props.children as React.ReactNode}</div>;
  },
  NavigationControl: () => <div data-testid="nav" />,
  ScaleControl: () => <div data-testid="scale" />,
}));

import { StacMap } from "@stac-higher/shared";

beforeEach(() => {
  mapProps.current = null;
});

describe("StacMap interaction props", () => {
  it("forwards the interactive layer ids and the cursor", () => {
    render(<StacMap interactiveLayerIds={["a-fill", "b-fill"]} cursor="pointer" />);

    expect(mapProps.current?.interactiveLayerIds).toEqual(["a-fill", "b-fill"]);
    expect(mapProps.current?.cursor).toBe("pointer");
  });

  it("forwards the mouse handlers untouched", () => {
    const onMouseMove = vi.fn();
    const onMouseLeave = vi.fn();
    render(<StacMap onMouseMove={onMouseMove} onMouseLeave={onMouseLeave} />);

    (mapProps.current?.onMouseMove as (e: unknown) => void)({ point: { x: 1, y: 2 } });
    (mapProps.current?.onMouseLeave as (e: unknown) => void)({});

    expect(onMouseMove).toHaveBeenCalledWith({ point: { x: 1, y: 2 } });
    expect(onMouseLeave).toHaveBeenCalledTimes(1);
  });

  it("leaves every new prop undefined when the caller omits it", () => {
    // The item, search, browse and preview pages pass none of these.
    render(<StacMap />);

    expect(mapProps.current?.interactiveLayerIds).toBeUndefined();
    expect(mapProps.current?.cursor).toBeUndefined();
    expect(mapProps.current?.onMouseMove).toBeUndefined();
    expect(screen.getByTestId("map")).toBeTruthy();
  });
});
