/**
 * K-4 review A8: a refused cancel (409 — the run just finished) still
 * refreshes the process's runs, so the row stops offering Cancel.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { processKeys } from "@/lib/query/keys";
import { useCancelRun } from "@/lib/processes/queries";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("useCancelRun", () => {
  it("invalidates the runs even when the cancel is refused", async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const spy = vi.spyOn(client, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({ error: "this one is 'succeeded'", code: "run_not_cancellable" }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    const { result } = renderHook(() => useCancelRun(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ id: "p1", runId: "r1" }).catch(() => undefined);
    });
    const keys = spy.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(processKeys.runs("p1")));
  });
});
