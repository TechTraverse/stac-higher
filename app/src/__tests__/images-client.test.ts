import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ImageApiError,
  addImage,
  getImagePolicy,
  getImageScan,
  imageListSearch,
  listImages,
  requestRescan,
} from "@/lib/images/api";
import {
  hasScanInFlight,
  isScanTerminal,
  listRefetchInterval,
  scanRefetchInterval,
} from "@/lib/images/queries";
import { imageKeys } from "@/lib/query/keys";
import type { Image, ImageScan } from "@/lib/images/types";

const fetchMock = vi.fn();

function reply(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("images client", () => {
  it("builds the list query string from the filters it was given", async () => {
    expect(imageListSearch({})).toBe("");
    expect(imageListSearch({ status: "flagged", q: "satpy", in_use: true })).toBe(
      "?status=flagged&q=satpy&in_use=true",
    );
    fetchMock.mockResolvedValue(reply(200, { images: [], scan_window_days: 30 }));
    expect(await listImages({ in_use: false })).toEqual({ images: [], scan_window_days: 30 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/images?in_use=false");
  });

  it("POSTs an add and returns the 202 body", async () => {
    const body = {
      id: "img-1",
      image_id: "img-1",
      scan_id: "scan-1",
      deduplicated: false,
      reference: "docker.io/library/python",
      tag: "latest",
    };
    fetchMock.mockResolvedValue(reply(202, body));
    expect(await addImage({ reference: "python" })).toEqual(body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/images");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ reference: "python" });
    expect(init.credentials).toBe("same-origin");
  });

  it("omits tag from the wire body when the caller left it blank", async () => {
    fetchMock.mockResolvedValue(
      reply(202, {
        id: "img-2",
        image_id: "img-2",
        scan_id: "scan-2",
        deduplicated: false,
        reference: "docker.io/library/python",
        tag: "latest",
      }),
    );
    await addImage({ reference: "x", tag: "" });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ reference: "x" });
  });

  it("surfaces the server's error and code", async () => {
    fetchMock.mockResolvedValue(
      reply(422, { error: "quay.io is not an allowed registry", code: "registry_not_allowed" }),
    );
    const err = await addImage({ reference: "quay.io/x/y" }).catch((e) => e);
    expect(err).toBeInstanceOf(ImageApiError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("registry_not_allowed");
    expect(err.message).toBe("quay.io is not an allowed registry");
  });

  it("encodes ids into the scan-poll, rescan and policy paths", async () => {
    fetchMock.mockResolvedValue(reply(200, {}));
    await getImageScan("a/b", "c d");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/images/a%2Fb/scans/c%20d");
    fetchMock.mockResolvedValue(reply(202, {}));
    await requestRescan("img-1");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/images/img-1/rescan");
    expect(fetchMock.mock.calls[1][1].method).toBe("POST");
    fetchMock.mockResolvedValue(reply(200, {}));
    await getImagePolicy();
    expect(fetchMock.mock.calls[2][0]).toBe("/api/processes/image-policy");
  });
});

describe("image query helpers and keys", () => {
  it("stops polling at a terminal scan status", () => {
    expect(isScanTerminal("done")).toBe(true);
    expect(isScanTerminal("failed")).toBe(true);
    expect(isScanTerminal("pending")).toBe(false);
    expect(isScanTerminal("running")).toBe(false);
    expect(isScanTerminal(undefined)).toBe(false);
  });

  it("keeps the list polling while any image is waiting for or in a scan", () => {
    const row = (status: Image["status"]) => ({ status }) as Image;
    expect(hasScanInFlight([row("approved"), row("pending")])).toBe(true);
    expect(hasScanInFlight([row("approved"), row("scanning")])).toBe(true);
    expect(hasScanInFlight([row("approved"), row("rejected")])).toBe(false);
    expect(hasScanInFlight(undefined)).toBe(false);
  });

  it("stops polling a scan on a persistent error, even with stale data left over", () => {
    const scan = (status: ImageScan["status"]) => ({ scan: { status } as ImageScan, image: null });
    expect(scanRefetchInterval({ status: "error", data: undefined })).toBe(false);
    // A prior success cached `scan`, then the poll started erroring: still stop.
    expect(scanRefetchInterval({ status: "error", data: scan("running") })).toBe(false);
    expect(scanRefetchInterval({ status: "success", data: scan("running") })).toBe(2_000);
    expect(scanRefetchInterval({ status: "success", data: scan("done") })).toBe(false);
  });

  it("stops polling the list on a persistent error, even with a stale scan-in-flight row", () => {
    expect(listRefetchInterval({ status: "error", data: undefined })).toBe(false);
    expect(
      listRefetchInterval({
        status: "error",
        data: { images: [{ status: "pending" } as Image], scan_window_days: 30 },
      }),
    ).toBe(false);
    expect(
      listRefetchInterval({
        status: "success",
        data: { images: [{ status: "pending" } as Image], scan_window_days: 30 },
      }),
    ).toBe(10_000);
    expect(
      listRefetchInterval({
        status: "success",
        data: { images: [{ status: "approved" } as Image], scan_window_days: 30 },
      }),
    ).toBe(false);
  });

  it("keys every image query under one prefix, catalog-agnostic", () => {
    expect(imageKeys.all()).toEqual(["images"]);
    expect(imageKeys.list({ status: "flagged" })).toEqual(["images", "list", { status: "flagged" }]);
    expect(imageKeys.detail("img-1")).toEqual(["images", "detail", "img-1"]);
    expect(imageKeys.scan("img-1", "scan-1")).toEqual(["images", "scan", "img-1", "scan-1"]);
    expect(imageKeys.policy()).toEqual(["images", "policy"]);
  });
});
