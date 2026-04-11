import { describe, it, expect, vi, beforeEach } from "vitest";
import { atom, computed } from "nanostores";
import { stacFetch } from "@/lib/stac-api/client";
import { StacApiError } from "@/lib/stac-api/types";

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

const $endpoints = atom([
  { id: "1", name: "Test", url: "http://localhost:8082", isDefault: true },
]);
const $activeEndpointId = atom("1");
const $mockActiveEndpoint = computed(
  [$endpoints, $activeEndpointId],
  (endpoints, id) => endpoints.find((e) => e.id === id) ?? null,
);

vi.mock("@/stores/endpointStore", () => ({
  get $activeEndpoint() {
    return $mockActiveEndpoint;
  },
}));

function jsonResponse(data: unknown, status = 200, statusText = "OK") {
  return new Response(JSON.stringify(data), {
    status,
    statusText,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockFetch.mockReset();
});

describe("stacFetch", () => {
  describe("URL construction", () => {
    it("uses active endpoint URL as base", async () => {
      mockFetch.mockResolvedValue(jsonResponse({ collections: [] }));
      await stacFetch("/collections");
      expect(mockFetch).toHaveBeenCalledWith(
        "http://localhost:8082/collections",
        expect.any(Object),
      );
    });

    it("uses override endpointUrl when provided", async () => {
      mockFetch.mockResolvedValue(jsonResponse({ ok: true }));
      await stacFetch("/", { endpointUrl: "http://other:9090" });
      expect(mockFetch).toHaveBeenCalledWith(
        "http://other:9090/",
        expect.any(Object),
      );
    });

    it("strips trailing slashes from endpoint URL", async () => {
      mockFetch.mockResolvedValue(jsonResponse({}));
      await stacFetch("/collections", { endpointUrl: "http://host:8080///" });
      expect(mockFetch).toHaveBeenCalledWith(
        "http://host:8080/collections",
        expect.any(Object),
      );
    });
  });

  describe("request options", () => {
    it("defaults to GET method", async () => {
      mockFetch.mockResolvedValue(jsonResponse({}));
      await stacFetch("/");
      expect(mockFetch).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ method: "GET" }),
      );
    });

    it("sets Accept header", async () => {
      mockFetch.mockResolvedValue(jsonResponse({}));
      await stacFetch("/");
      const call = mockFetch.mock.calls[0][1];
      expect(call.headers.Accept).toBe("application/json");
    });

    it("sets Content-Type when body is provided", async () => {
      mockFetch.mockResolvedValue(jsonResponse({}));
      await stacFetch("/collections", { method: "POST", body: { id: "test" } });
      const call = mockFetch.mock.calls[0][1];
      expect(call.headers["Content-Type"]).toBe("application/json");
      expect(call.body).toBe(JSON.stringify({ id: "test" }));
    });

    it("does not set Content-Type when no body", async () => {
      mockFetch.mockResolvedValue(jsonResponse({}));
      await stacFetch("/");
      const call = mockFetch.mock.calls[0][1];
      expect(call.headers["Content-Type"]).toBeUndefined();
    });

    it("passes signal through", async () => {
      mockFetch.mockResolvedValue(jsonResponse({}));
      const controller = new AbortController();
      await stacFetch("/", { signal: controller.signal });
      const call = mockFetch.mock.calls[0][1];
      expect(call.signal).toBe(controller.signal);
    });
  });

  describe("successful responses", () => {
    it("parses JSON response", async () => {
      mockFetch.mockResolvedValue(jsonResponse({ collections: ["a", "b"] }));
      const result = await stacFetch("/collections");
      expect(result).toEqual({ collections: ["a", "b"] });
    });

    it("returns undefined for 204 No Content", async () => {
      mockFetch.mockResolvedValue(new Response(null, { status: 204, statusText: "No Content" }));
      const result = await stacFetch("/collections/test", { method: "DELETE" });
      expect(result).toBeUndefined();
    });
  });

  describe("error handling", () => {
    it("throws StacApiError on 404", async () => {
      mockFetch.mockResolvedValue(
        jsonResponse({ detail: "Not Found" }, 404, "Not Found"),
      );
      try {
        await stacFetch("/collections/missing");
        expect.fail("should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(StacApiError);
        expect((e as StacApiError).status).toBe(404);
        expect((e as StacApiError).detail).toBe("Not Found");
      }
    });

    it("throws StacApiError on 500", async () => {
      mockFetch.mockResolvedValue(
        jsonResponse({ message: "Internal error" }, 500, "Internal Server Error"),
      );
      try {
        await stacFetch("/search", { method: "POST", body: {} });
      } catch (e) {
        expect(e).toBeInstanceOf(StacApiError);
        expect((e as StacApiError).status).toBe(500);
        expect((e as StacApiError).detail).toBe("Internal error");
      }
    });

    it("handles error response with non-JSON body", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: () => Promise.reject(new Error("not json")),
        text: () => Promise.resolve("Bad Gateway"),
      });
      try {
        await stacFetch("/");
        expect.fail("should have thrown");
      } catch (e) {
        expect(e).toBeInstanceOf(StacApiError);
        expect((e as StacApiError).status).toBe(502);
        expect((e as StacApiError).detail).toBe("Bad Gateway");
      }
    });

    it("extracts detail from error body", async () => {
      mockFetch.mockResolvedValue(
        jsonResponse({ detail: "Collection already exists" }, 409, "Conflict"),
      );
      try {
        await stacFetch("/collections", { method: "POST", body: {} });
      } catch (e) {
        expect((e as StacApiError).detail).toBe("Collection already exists");
      }
    });

    it("falls back to message field in error body", async () => {
      mockFetch.mockResolvedValue(
        jsonResponse({ message: "Rate limited" }, 429, "Too Many Requests"),
      );
      try {
        await stacFetch("/search", { method: "POST", body: {} });
      } catch (e) {
        expect((e as StacApiError).detail).toBe("Rate limited");
      }
    });

    it("stringifies error body when no detail or message", async () => {
      mockFetch.mockResolvedValue(
        jsonResponse({ code: "ERR_UNKNOWN" }, 400, "Bad Request"),
      );
      try {
        await stacFetch("/");
      } catch (e) {
        expect((e as StacApiError).detail).toBe('{"code":"ERR_UNKNOWN"}');
      }
    });
  });
});
