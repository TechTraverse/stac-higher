// @vitest-environment node
/**
 * Migration 030 shape pins (C-1, container-images spec §4). Read as text, like
 * the 022/026/028 pins. The CHECK lists are compared against the
 * image-status.json fixture, so a status cannot drift between the DDL and the
 * two runtimes.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { migrationEntry, migrationSource } from "./helpers/migration-source";

const STATUS = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../tests/contract-fixtures/image-status.json", import.meta.url)),
    "utf8",
  ),
) as { image_statuses: string[]; scan_kinds: string[]; scan_statuses: string[] };

const sql = migrationEntry("030_container_images");

function checkList(constraint: string, column: string): string[] {
  const match = sql.match(
    new RegExp(`${constraint}\\s+CHECK \\(${column} IN \\(([^)]+)\\)\\)`),
  );
  expect(match, constraint).not.toBeNull();
  return match![1].split(",").map((s) => s.trim().replace(/'/g, ""));
}

describe("migration 030 (container images — C-1)", () => {
  it("runs after X-4's 028 (and after K-3's 029 whenever that lands)", () => {
    const at = migrationSource.indexOf('"030_container_images"');
    expect(at).toBeGreaterThan(migrationSource.indexOf('"028_builtin_processes"'));
    const k3 = migrationSource.indexOf('"029_');
    if (k3 !== -1) expect(at).toBeGreaterThan(k3);
  });

  it("creates both tables in stac_higher", () => {
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.container_images (");
    expect(sql).toContain("CREATE TABLE IF NOT EXISTS stac_higher.image_scans (");
  });

  it("constrains every status/kind to exactly the fixture's vocabulary", () => {
    expect(checkList("container_images_status_check", "status")).toEqual(STATUS.image_statuses);
    expect(checkList("image_scans_kind_check", "kind")).toEqual(STATUS.scan_kinds);
    expect(checkList("image_scans_status_check", "status")).toEqual(STATUS.scan_statuses);
  });

  it("keys the registry on (reference, digest) with nulls distinct (spec §4.1)", () => {
    expect(sql).toContain("CONSTRAINT container_images_reference_digest_key UNIQUE (reference, digest)");
    expect(sql).not.toContain("NULLS NOT DISTINCT");
  });

  it("allows a NULL digest only while no digest can exist yet", () => {
    expect(sql).toMatch(
      /container_images_digest_required_check CHECK \(\s*digest IS NOT NULL OR status IN \('pending','scanning','scan_failed','revoked'\)\s*\)/,
    );
    expect(sql).toContain("digest ~ '^sha256:[a-f0-9]{64}$'");
  });

  it("keeps an exception all-or-nothing (spec §4.4: expires_at is required)", () => {
    expect(sql).toContain("container_images_exception_check");
    expect(sql).toContain("(exception_at IS NULL) = (exception_expires_at IS NULL)");
  });

  it("never lets a group credential vanish from under an image", () => {
    expect(sql).toMatch(
      /registry_connection_id uuid\s+REFERENCES stac_higher\.connections\(id\) ON DELETE RESTRICT/,
    );
  });

  it("cascades scans with their image and indexes the drain and the rescan tick", () => {
    expect(sql).toMatch(
      /image_id uuid NOT NULL\s+REFERENCES stac_higher\.container_images\(id\) ON DELETE CASCADE/,
    );
    expect(sql).toMatch(/image_scans_pending_idx\s+ON stac_higher\.image_scans \(requested_at\)\s+WHERE status = 'pending'/);
    expect(sql).toMatch(
      /container_images_rescan_idx\s+ON stac_higher\.container_images \(last_scanned_at\)\s+WHERE status IN \('approved','flagged'\)/,
    );
  });

  it("indexes the revision snapshot so 'in use by N processes' is one join", () => {
    expect(sql).toContain("process_revisions_image_id_idx");
    expect(sql).toContain("((runtime->'image'->>'id'))");
  });

  it("admits the registry protocol without dropping any existing one", () => {
    expect(sql).toContain(
      "CHECK (protocol IN ('ssh','sftp','ftp','ftps','s3','stac-api','registry'))",
    );
  });

  it("has no last_scan_id FK (it would be circular with image_scans.image_id)", () => {
    expect(sql).toMatch(/last_scan_id uuid,/);
    expect(sql).not.toMatch(/last_scan_id uuid[^,\n]*REFERENCES/);
  });
});
