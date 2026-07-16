import { describe, it, expect, vi } from "vitest";
import { uploadAsset } from "@/lib/storage/upload";

function file(name = "B04.tif", type = "image/tiff"): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type });
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("uploadAsset", () => {
  it("presigns then PUTs the bytes and returns the href", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, {
          uploads: [{ url: "http://minio/put-url", href: "/api/assets/c/i/B04.tif" }],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));

    const result = await uploadAsset(file(), { collection: "c", item: "i" }, fetchImpl as never);

    expect(result).toEqual({
      href: "/api/assets/c/i/B04.tif",
      filename: "B04.tif",
      contentType: "image/tiff",
    });
    // hop 1: presign request to the app; hop 2: PUT to the returned URL
    expect(fetchImpl).toHaveBeenNthCalledWith(1, "/api/uploads", expect.objectContaining({ method: "POST" }));
    expect(fetchImpl).toHaveBeenNthCalledWith(2, "http://minio/put-url", expect.objectContaining({ method: "PUT" }));
  });

  it("throws the server error when presign fails (and never PUTs)", async () => {
    const fetchImpl = vi.fn().mockResolvedValueOnce(jsonResponse(403, { error: "forbidden" }));
    await expect(
      uploadAsset(file(), { collection: "c", item: "i" }, fetchImpl as never),
    ).rejects.toThrow("forbidden");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("throws when the storage PUT fails", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(200, { uploads: [{ url: "http://minio/put-url", href: "/api/assets/c/i/B04.tif" }] }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 500 }));
    await expect(
      uploadAsset(file(), { collection: "c", item: "i" }, fetchImpl as never),
    ).rejects.toThrow("Upload to storage failed (500)");
  });
});
