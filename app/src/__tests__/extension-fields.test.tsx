/**
 * I-67 regression: ExtensionFields renders a pipeline-shaped projection
 * extension schema (remote GeoJSON $ref) without crashing and without
 * fetching geojson.org, degrades the unresolvable PROJJSON $ref to a raw-JSON
 * field, and renders no <form> element of its own (the RHF page form is the
 * only form — the nested-form hydration warning fix).
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ExtensionFields } from "@/components/extensions/ExtensionFields";
import {
  projectionSchemaFixture,
  projItemProperties,
  PROJECTION_SCHEMA_URL,
  PROJJSON_SCHEMA_URL,
} from "./helpers/projection-fixture";

const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
  const { url } = JSON.parse(String(init?.body)) as { url: string };
  if (url === PROJECTION_SCHEMA_URL) {
    return new Response(JSON.stringify(projectionSchemaFixture), {
      status: 200,
      headers: { "Content-Type": "application/schema+json" },
    });
  }
  // Everything else (e.g. proj.org PROJJSON) is unreachable in this test.
  return new Response(JSON.stringify({ error: "unreachable" }), { status: 502 });
});

function requestedUrls(): string[] {
  return fetchMock.mock.calls.map(
    ([, init]) => (JSON.parse(String(init?.body)) as { url: string }).url,
  );
}

function renderPanel(onChange = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <ExtensionFields
        schemaUrls={[PROJECTION_SCHEMA_URL]}
        value={{ [PROJECTION_SCHEMA_URL]: projItemProperties }}
        onChange={onChange}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onChange };
}

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

describe("ExtensionFields — projection extension with remote GeoJSON $ref (I-67)", () => {
  it("renders the proj fields without crashing and without fetching geojson.org", async () => {
    renderPanel();

    // The extracted fields render (proj:code carries this title).
    expect(await screen.findByText("Projection code")).toBeDefined();

    // The GeoJSON $ref resolved from the local bundle — no network attempt.
    expect(requestedUrls()).not.toContain("https://geojson.org/schema/Geometry.json");
  });

  it("falls back to a raw-JSON field for the unresolvable PROJJSON $ref", async () => {
    renderPanel();
    await screen.findByText("Projection code");

    expect(requestedUrls()).toContain(PROJJSON_SCHEMA_URL);
    expect(
      screen.getByText(/fall back to raw JSON editing/i),
    ).toBeDefined();
    expect(
      await screen.findByText("Coordinate Reference System in PROJJSON format"),
    ).toBeDefined();
  });

  it("renders no <form> element (RJSF uses a div inside the RHF page form)", async () => {
    const { container } = renderPanel();
    await screen.findByText("Projection code");
    expect(container.querySelector("form")).toBeNull();
  });

  it("round-trips raw-JSON edits on the degraded PROJJSON field", async () => {
    const { onChange } = renderPanel();
    await screen.findByText("Projection code");

    const label = await screen.findByText(
      "Coordinate Reference System in PROJJSON format",
    );
    const textarea = document.getElementById(
      label.getAttribute("for") ?? "",
    ) as HTMLTextAreaElement;
    expect(textarea).not.toBeNull();

    fireEvent.change(textarea, { target: { value: '{"type": "GeographicCRS"}' } });
    await waitFor(() => {
      const last = onChange.mock.calls.at(-1)?.[0] as Record<
        string,
        Record<string, unknown>
      >;
      expect(last[PROJECTION_SCHEMA_URL]["proj:projjson"]).toEqual({
        type: "GeographicCRS",
      });
    });

    // Invalid JSON is kept as a local draft, never propagated.
    const callsBefore = onChange.mock.calls.length;
    fireEvent.change(textarea, { target: { value: "{not json" } });
    expect(await screen.findByText(/Invalid JSON/)).toBeDefined();
    expect(onChange.mock.calls.length).toBe(callsBefore);
  });

  it("propagates edits through onChange for the merge-into-properties save path", async () => {
    const { onChange } = renderPanel();
    const input = (await screen.findByLabelText("Projection code")) as HTMLInputElement;
    expect(input.value).toBe("EPSG:32633");

    fireEvent.change(input, { target: { value: "EPSG:4326" } });

    await waitFor(() => {
      expect(onChange).toHaveBeenCalled();
      const last = onChange.mock.calls.at(-1)?.[0] as Record<
        string,
        Record<string, unknown>
      >;
      expect(last[PROJECTION_SCHEMA_URL]["proj:code"]).toBe("EPSG:4326");
      // Untouched sibling values survive the round-trip.
      expect(last[PROJECTION_SCHEMA_URL]["proj:shape"]).toEqual([512, 512]);
    });
  });
});
