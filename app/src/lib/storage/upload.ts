/**
 * Client-side asset upload orchestration (ROADMAP Phase 3, flow C).
 *
 * Two hops: ask the app for a presigned PUT URL, then PUT the bytes straight to
 * object storage. Lives in `lib/` (not the component) so it is reusable — the
 * Phase 7 push path needs the same flow — and unit-testable. `fetchImpl` is
 * injectable for tests; production uses the global `fetch`.
 */

export interface UploadedAsset {
  href: string;
  filename: string;
  contentType: string;
}

export async function uploadAsset(
  file: File,
  target: { collection: string; item: string },
  fetchImpl: typeof fetch = fetch,
): Promise<UploadedAsset> {
  const presignRes = await fetchImpl("/api/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      collection: target.collection,
      item: target.item,
      files: [{ filename: file.name, contentType: file.type || undefined }],
    }),
  });
  if (!presignRes.ok) {
    const body = await presignRes.json().catch(() => ({}));
    throw new Error(body.error ?? `Presign failed (${presignRes.status})`);
  }

  const { uploads } = (await presignRes.json()) as {
    uploads: { url: string; href: string }[];
  };
  const { url, href } = uploads[0];

  const putRes = await fetchImpl(url, {
    method: "PUT",
    body: file,
    headers: file.type ? { "Content-Type": file.type } : undefined,
  });
  if (!putRes.ok) {
    throw new Error(`Upload to storage failed (${putRes.status})`);
  }

  return { href, filename: file.name, contentType: file.type };
}
