/**
 * Cube sink client hooks (Z-8b): the 404 = "no sink yet" mapping, typed API
 * errors (code + zod details), and the PATCH body.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cubeSinkKeys } from "@/lib/query/keys";
import type { ReactNode } from "react";
import {
  CubeSinkApiError,
  useCubeSink,
  useFreshCubeSink,
  usePatchCubeSink,
  usePutCubeSink,
  useReferenceSources,
} from "@/lib/cubes/queries";

const fetchMock = vi.fn();

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function json(status: number, body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("useCubeSink", () => {
  it("maps a 404 to null (no sink yet)", async () => {
    fetchMock.mockReturnValue(json(404, { error: "Cube sink not found" }));
    const { result } = renderHook(() => useCubeSink("c"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("returns the view on 200", async () => {
    const view = { sink: { id: "s", last_snapshot_id: null }, ledger: { counts: {}, recent: [] } };
    fetchMock.mockReturnValue(json(200, view));
    const { result } = renderHook(() => useCubeSink("c"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(view);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/collections/c/cube-sink");
  });

  it("surfaces other failures as errors", async () => {
    fetchMock.mockReturnValue(json(500, { error: "boom" }));
    const { result } = renderHook(() => useCubeSink("c"), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(CubeSinkApiError);
  });
});

describe("cubeSinkKeys", () => {
  it("never collides with the reference-sources key, even for that collection id", () => {
    expect(cubeSinkKeys.detail("reference-sources")).not.toEqual(cubeSinkKeys.referenceSources());
    expect(cubeSinkKeys.detail("c")).toEqual(["cube-sink", "detail", "c"]);
  });
});

describe("useFreshCubeSink", () => {
  it("fetches now even when the cache is fresh, and updates the cache", async () => {
    const view = (n: number) => ({ sink: { id: "s", last_snapshot_id: null }, ledger: { counts: { pending: n }, recent: [] } });
    fetchMock.mockReturnValueOnce(json(200, view(0))).mockReturnValueOnce(json(200, view(2)));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } });
    const w = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const live = renderHook(() => useCubeSink("c"), { wrapper: w });
    await waitFor(() => expect(live.result.current.isSuccess).toBe(true));
    const fresh = renderHook(() => useFreshCubeSink("c"), { wrapper: w });
    const got = await fresh.result.current();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(got?.ledger.counts.pending).toBe(2);
    expect(
      (client.getQueryData(cubeSinkKeys.detail("c")) as { ledger: { counts: { pending: number } } }).ledger.counts.pending,
    ).toBe(2);
  });
});

describe("useReferenceSources", () => {
  it("unwraps the collections array and does not fetch while disabled", async () => {
    const rows = [{ collection_id: "a", title: null, associations: [], refusal: null }];
    fetchMock.mockReturnValue(json(200, { collections: rows }));
    const off = renderHook(() => useReferenceSources(false), { wrapper: wrapper() });
    expect(off.result.current.fetchStatus).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
    const on = renderHook(() => useReferenceSources(true), { wrapper: wrapper() });
    await waitFor(() => expect(on.result.current.isSuccess).toBe(true));
    expect(on.result.current.data).toEqual(rows);
  });
});

describe("mutations", () => {
  const body = {
    source_collection_id: "src",
    enabled: true,
    config: {
      append_dim: "t",
      variables: ["CMI"],
      loadable_variables: ["t"],
    },
  };

  it("keeps the code of a 422", async () => {
    fetchMock.mockReturnValue(
      json(422, { error: "not s3", code: "unsupported_source_protocol" }),
    );
    const { result } = renderHook(() => usePutCubeSink("c"), { wrapper: wrapper() });
    act(() => result.current.mutate(body));
    await waitFor(() => expect(result.current.isError).toBe(true));
    const err = result.current.error as CubeSinkApiError;
    expect(err).toBeInstanceOf(CubeSinkApiError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("unsupported_source_protocol");
    expect(err.message).toBe("not s3");
  });

  it("keeps the details of a 400", async () => {
    const details = [{ path: ["config", "window", "max_steps"], message: "too small" }];
    fetchMock.mockReturnValue(json(400, { error: "Validation failed", details }));
    const { result } = renderHook(() => usePutCubeSink("c"), { wrapper: wrapper() });
    act(() => result.current.mutate(body));
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as CubeSinkApiError).details).toEqual(details);
    expect(fetchMock.mock.calls[0][1].method).toBe("PUT");
  });

  it("PATCH sends {enabled}", async () => {
    fetchMock.mockReturnValue(json(200, { sink: { id: "s" } }));
    const { result } = renderHook(() => usePatchCubeSink("c"), { wrapper: wrapper() });
    await act(async () => {
      await result.current.mutateAsync({ enabled: false });
    });
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe('{"enabled":false}');
  });
});
