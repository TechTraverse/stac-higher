import { useState } from "react";

/**
 * STAC token pagination. The API hands back `next`/`prev` links rather than
 * offsets, so paging back means remembering the tokens we came through.
 * Shared by the product item list and the read-only catalog browser.
 *
 * The caller reads `token` for its query and passes the response's `next`
 * token into `goNext` — the hook cannot read the response itself, since the
 * token it owns is what produced it.
 */
export function extractToken(
  links: Array<{ href: string; rel: string }> | undefined,
  rel: string,
): string | undefined {
  const link = links?.find((l) => l.rel === rel);
  if (!link) return undefined;
  try {
    const url = new URL(link.href, "http://localhost");
    return url.searchParams.get("token") ?? undefined;
  } catch {
    return undefined;
  }
}

export function useTokenPaging() {
  const [token, setToken] = useState<string | undefined>();
  const [history, setHistory] = useState<string[]>([]);

  return {
    token,
    page: history.length + 1,
    hasPrev: history.length > 0,
    goNext: (nextToken: string | undefined) => {
      if (!nextToken) return;
      setHistory((prev) => [...prev, token ?? ""]);
      setToken(nextToken);
    },
    goPrev: () => {
      setHistory((prev) => {
        if (prev.length === 0) return prev;
        const rest = [...prev];
        const prevToken = rest.pop();
        setToken(prevToken || undefined);
        return rest;
      });
    },
  };
}
