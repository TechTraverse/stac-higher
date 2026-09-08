/**
 * `lib/browse/paths.ts` — the seam that keeps the product surface and the
 * read-only catalog browser from crossing (UI-10), and makes a browse link
 * shareable (UI-15).
 *
 * The rules worth pinning: a catalog id is browser-local so `?src=` is the
 * identity that travels; `src` comes from a link someone else wrote, so it is
 * validated before it is ever shown; and resolution must not silently browse a
 * DIFFERENT catalog that happens to share the sender's id.
 */
import { describe, it, expect } from "vitest";
import {
  browseCollectionPath,
  browseTarget,
  browseCollectionsPath,
  browseItemPath,
  browseItemsPath,
  collectionHref,
  itemHref,
  normalizeCatalogUrl,
  parseSrc,
  resolveBrowseCatalog,
  resolveItemLink,
} from "@/lib/browse/paths";
import type { StacCatalog } from "@/stores/catalogStore";

const EXTERNAL: StacCatalog = {
  id: "ext-1",
  name: "NOAA",
  url: "https://stac.example.com",
  isDefault: false,
};

const BUILT_IN: StacCatalog = {
  id: "built-in",
  name: "Built-in Catalog",
  url: "http://localhost:8081",
  isDefault: true,
  builtIn: true,
};

describe("browse paths", () => {
  it("carries the catalog URL as ?src= so the link travels", () => {
    expect(browseCollectionsPath(EXTERNAL)).toBe(
      "/catalogs/ext-1/collections?src=https%3A%2F%2Fstac.example.com",
    );
    expect(browseCollectionPath(EXTERNAL, "ak_bare_earth")).toBe(
      "/catalogs/ext-1/collections/ak_bare_earth?src=https%3A%2F%2Fstac.example.com",
    );
    expect(browseItemsPath(EXTERNAL, "ak_bare_earth")).toBe(
      "/catalogs/ext-1/collections/ak_bare_earth/items?src=https%3A%2F%2Fstac.example.com",
    );
    expect(browseItemPath(EXTERNAL, "ak_bare_earth", "tile-1")).toBe(
      "/catalogs/ext-1/collections/ak_bare_earth/items/tile-1?src=https%3A%2F%2Fstac.example.com",
    );
  });

  it("omits ?src= when the caller has no URL to offer", () => {
    expect(browseCollectionsPath({ id: "ext-1" })).toBe(
      "/catalogs/ext-1/collections",
    );
  });

  it("encodes ids that would otherwise break the path", () => {
    expect(browseCollectionPath({ id: "a/b" }, "c d/e")).toBe(
      "/catalogs/a%2Fb/collections/c%20d%2Fe",
    );
  });
});

describe("collectionHref / itemHref", () => {
  it("sends the built-in catalog to the product routes", () => {
    expect(collectionHref(BUILT_IN, "prod-a")).toBe("/collections/prod-a");
    expect(itemHref(BUILT_IN, "prod-a", "i1")).toBe(
      "/collections/prod-a/items/i1",
    );
  });

  it("sends every other catalog to its own read-only browser", () => {
    expect(collectionHref(EXTERNAL, "c1")).toMatch(
      /^\/catalogs\/ext-1\/collections\/c1\?src=/,
    );
    expect(itemHref(EXTERNAL, "c1", "i1")).toMatch(
      /^\/catalogs\/ext-1\/collections\/c1\/items\/i1\?src=/,
    );
  });

  it("falls back to the product routes with no catalog at all", () => {
    expect(collectionHref(null, "c1")).toBe("/collections/c1");
    expect(itemHref(undefined, "c1", "i1")).toBe("/collections/c1/items/i1");
  });
});

describe("parseSrc", () => {
  it("accepts http and https, normalizing trailing slashes", () => {
    expect(parseSrc("https://stac.example.com/")).toBe("https://stac.example.com");
    expect(parseSrc("http://localhost:8082//")).toBe("http://localhost:8082");
  });

  it("rejects anything that is not an http(s) URL", () => {
    // `src` is attacker-controllable — it arrives in a link someone else wrote.
    expect(parseSrc("javascript:alert(1)")).toBeNull();
    expect(parseSrc("file:///etc/passwd")).toBeNull();
    expect(parseSrc("data:text/html,<script>")).toBeNull();
    expect(parseSrc("not a url")).toBeNull();
    expect(parseSrc("")).toBeNull();
    expect(parseSrc(null)).toBeNull();
    expect(parseSrc(undefined)).toBeNull();
  });
});

describe("resolveBrowseCatalog", () => {
  const catalogs = [BUILT_IN, EXTERNAL];

  it("is undefined while the persistent store is still empty", () => {
    expect(resolveBrowseCatalog([], "ext-1", null)).toBeUndefined();
  });

  it("resolves by id for a local link with no src", () => {
    expect(resolveBrowseCatalog(catalogs, "ext-1", null)).toBe(EXTERNAL);
    expect(resolveBrowseCatalog(catalogs, "nope", null)).toBeNull();
  });

  it("resolves a shared link by URL, under the recipient's own id", () => {
    // The sender's id means nothing here; the recipient filed the same
    // catalog under "ext-1".
    expect(
      resolveBrowseCatalog(catalogs, "92f1f883-sender-id", "https://stac.example.com"),
    ).toBe(EXTERNAL);
  });

  it("ignores the path id when src is present, rather than browsing a same-id stranger", () => {
    const decoy: StacCatalog = {
      id: "ext-1",
      name: "Someone else's catalog",
      url: "https://different.example.com",
      isDefault: false,
    };
    expect(
      resolveBrowseCatalog([decoy], "ext-1", "https://stac.example.com"),
    ).toBeNull();
  });

  it("is null for a shared link this browser has not added — the add prompt's cue", () => {
    expect(
      resolveBrowseCatalog(catalogs, "x", "https://unknown.example.com"),
    ).toBeNull();
  });

  it("matches regardless of trailing slashes on either side", () => {
    const trailing: StacCatalog = { ...EXTERNAL, url: "https://stac.example.com/" };
    expect(
      resolveBrowseCatalog([trailing], "x", "https://stac.example.com"),
    ).toBe(trailing);
  });
});

describe("browseTarget", () => {
  // Regression: the pages build their breadcrumb and card hrefs as JSX PROPS,
  // so those expressions run BEFORE BrowseFrame can early-return on a missing
  // catalog. A `catalog!` assertion there crashed the whole island with
  // "Cannot read properties of null (reading 'id')" on exactly the case this
  // feature exists for — a shared link the recipient has not added yet.
  it("falls back to the route's own id and src while the catalog is unresolved", () => {
    const target = browseTarget(null, "sender-id", "https://stac.example.com/");
    expect(browseCollectionsPath(target)).toBe(
      "/catalogs/sender-id/collections?src=https%3A%2F%2Fstac.example.com",
    );
  });

  it("drops an unusable src rather than propagating it into links", () => {
    const target = browseTarget(undefined, "sender-id", "javascript:alert(1)");
    expect(browseCollectionsPath(target)).toBe("/catalogs/sender-id/collections");
  });

  it("prefers the resolved catalog once there is one", () => {
    const target = browseTarget(EXTERNAL, "sender-id", "https://stac.example.com");
    expect(browseCollectionsPath(target)).toMatch(/^\/catalogs\/ext-1\//);
  });
});

describe("normalizeCatalogUrl", () => {
  it("trims whitespace and trailing slashes only", () => {
    expect(normalizeCatalogUrl("  https://a.example.com///  ")).toBe(
      "https://a.example.com",
    );
    expect(normalizeCatalogUrl("https://a.example.com/stac")).toBe(
      "https://a.example.com/stac",
    );
  });
});

describe("resolveItemLink (D-2)", () => {
  const catalogs = [BUILT_IN, EXTERNAL];
  const OTHER: StacCatalog = {
    id: "ext-2",
    name: "Planetary",
    url: "https://planetarycomputer.example/api/stac/v1/",
    isDefault: false,
  };

  it("sends a root-relative href to the page's own catalog", () => {
    // D-1's default output on the product page.
    const t = resolveItemLink(
      { rel: "derived_from", href: "/collections/goes-abi-mcmipc/items/scene-1" },
      BUILT_IN,
      catalogs,
    );
    expect(t).toEqual({
      href: "/collections/goes-abi-mcmipc/items/scene-1",
      label: "goes-abi-mcmipc / scene-1",
      external: false,
    });
  });

  it("sends a root-relative href on a browse page to that catalog's browser", () => {
    const t = resolveItemLink(
      { rel: "derived_from", href: "/collections/c1/items/i1" },
      EXTERNAL,
      catalogs,
    );
    expect(t.external).toBe(false);
    expect(t.href).toBe(
      "/catalogs/ext-1/collections/c1/items/i1?src=https%3A%2F%2Fstac.example.com",
    );
  });

  it("matches an absolute href under the page catalog's URL", () => {
    const t = resolveItemLink(
      { rel: "derived_from", href: "https://stac.example.com/collections/c1/items/i1" },
      EXTERNAL,
      catalogs,
    );
    expect(t.external).toBe(false);
    expect(t.href).toMatch(/^\/catalogs\/ext-1\/collections\/c1\/items\/i1\?src=/);
  });

  it("matches an absolute href under ANOTHER configured catalog, trailing slash and all", () => {
    const t = resolveItemLink(
      {
        rel: "derived_from",
        href: "https://planetarycomputer.example/api/stac/v1/collections/s2/items/tile-9",
      },
      BUILT_IN,
      [...catalogs, OTHER],
    );
    expect(t).toEqual({
      // `?src=` carries the stored URL verbatim, trailing slash included — the
      // recipient's resolver normalises it (parseSrc), as for every browse link.
      href: "/catalogs/ext-2/collections/s2/items/tile-9?src=https%3A%2F%2Fplanetarycomputer.example%2Fapi%2Fstac%2Fv1%2F",
      label: "s2 / tile-9",
      external: false,
    });
  });

  it("maps an absolute href into the built-in catalog onto the product page", () => {
    const t = resolveItemLink(
      { rel: "derived_from", href: "http://localhost:8081/collections/prod/items/i1" },
      EXTERNAL,
      catalogs,
    );
    expect(t).toEqual({
      href: "/collections/prod/items/i1",
      label: "prod / i1",
      external: false,
    });
  });

  it("decodes the href's segments and re-encodes them for the route", () => {
    const t = resolveItemLink(
      { rel: "derived_from", href: "/collections/goes/items/a%20b%2Fc" },
      BUILT_IN,
      catalogs,
    );
    expect(t.label).toBe("goes / a b/c");
    expect(t.href).toBe("/collections/goes/items/a%20b%2Fc");
  });

  it("prefers the link's title as the label", () => {
    const t = resolveItemLink(
      { rel: "derived_from", href: "/collections/c/items/i", title: "Source scene" },
      BUILT_IN,
      catalogs,
    );
    expect(t.label).toBe("Source scene");
  });

  it("falls through to an external anchor when nothing matches", () => {
    const t = resolveItemLink(
      { rel: "derived_from", href: "https://elsewhere.example/stac/collections/c/items/i?x=1" },
      BUILT_IN,
      catalogs,
    );
    expect(t).toEqual({
      href: "https://elsewhere.example/stac/collections/c/items/i?x=1",
      label: "c / i",
      external: true,
    });
  });

  it("never fabricates an in-app route for a href that is not an item path", () => {
    expect(
      resolveItemLink({ rel: "derived_from", href: "/collections/c" }, BUILT_IN, catalogs).external,
    ).toBe(true);
    expect(
      resolveItemLink(
        { rel: "derived_from", href: "https://stac.example.com/collections/c/items/i/assets/a" },
        EXTERNAL,
        catalogs,
      ).external,
    ).toBe(true);
    expect(
      resolveItemLink({ rel: "derived_from", href: "/collections/c/items/%E0%A4%A" }, BUILT_IN, catalogs)
        .external,
    ).toBe(true);
  });

  it("does not match a catalog whose URL is merely a string prefix", () => {
    const decoy: StacCatalog = {
      id: "ext-3",
      name: "prefix",
      url: "https://stac.example",
      isDefault: false,
    };
    const t = resolveItemLink(
      { rel: "derived_from", href: "https://stac.example.com/collections/c/items/i" },
      null,
      [decoy],
    );
    expect(t.external).toBe(true);
  });
});
