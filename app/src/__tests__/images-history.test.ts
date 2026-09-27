/**
 * C-4 presentation helpers: the exception wording keyed on the date alone,
 * and the scan-history line (spec §9.2: "+2 high, −1 medium since
 * 2026-09-12"), built from the diff the drain stores (spec §8.2).
 */
import { describe, expect, it } from "vitest";
import {
  formatCountsDelta,
  isExceptionExpired,
  scanDiffSummary,
  scanTagDrift,
} from "@/components/images/format";
import type { ImageScan } from "@/lib/images/types";

const NOW = new Date("2026-09-27T12:00:00.000Z");
const ZERO = { critical: 0, high: 0, medium: 0, low: 0, negligible: 0, unknown: 0 };

function scan(overrides: Partial<ImageScan> = {}): ImageScan {
  return {
    id: "s-2",
    image_id: "img-1",
    kind: "rescan",
    status: "done",
    requested_by: "pipeline",
    requested_at: "2026-09-27T03:15:00.000Z",
    started_at: null,
    finished_at: "2026-09-27T03:20:00.000Z",
    result: null,
    findings_ref: null,
    log_ref: null,
    ...overrides,
  };
}

function withDiff(diff: Record<string, unknown>): ImageScan {
  return scan({
    result: {
      diff: {
        previous_scan_id: "s-1",
        new: [],
        resolved: [],
        newly_fixed: [],
        new_kev: [],
        verdict_changed: false,
        counts_delta: ZERO,
        ...diff,
      },
    },
  });
}

describe("isExceptionExpired (the date alone)", () => {
  it("is true once the expiry has passed, including exactly now (the gate's boundary)", () => {
    expect(isExceptionExpired("2026-09-27T11:59:59.000Z", NOW)).toBe(true);
    expect(isExceptionExpired(NOW.toISOString(), NOW)).toBe(true);
    expect(isExceptionExpired("2026-09-27T12:00:01.000Z", NOW)).toBe(false);
  });

  it("reads an unparseable date as expired, never as live", () => {
    expect(isExceptionExpired("not a date", NOW)).toBe(true);
  });
});

describe("formatCountsDelta", () => {
  it("names only the severities that moved, worst first, with a real minus sign", () => {
    expect(formatCountsDelta({ ...ZERO, high: 2, medium: -1 })).toBe("+2 high, −1 medium");
    expect(formatCountsDelta({ ...ZERO, critical: 1, low: -3 })).toBe("+1 critical, −3 low");
  });

  it("says so when nothing moved", () => {
    expect(formatCountsDelta(ZERO)).toBe("no change in counts");
  });
});

describe("scanDiffSummary", () => {
  const previous = scan({ id: "s-1", kind: "admission", finished_at: "2026-09-12T08:00:00.000Z" });

  it("dates the comparison by the previous scan when it is listed", () => {
    const s = withDiff({
      counts_delta: { ...ZERO, high: 2, medium: -1 },
      new_kev: ["CVE-2026-0003"],
      verdict_changed: true,
    });
    expect(scanDiffSummary(s, [s, previous])).toBe(
      "+2 high, −1 medium since 2026-09-12 · 1 new KEV, verdict changed",
    );
  });

  it("falls back to 'the previous scan' when it is not in the list", () => {
    const s = withDiff({ newly_fixed: ["CVE-2026-0002"] });
    expect(scanDiffSummary(s, [s])).toBe(
      "no change in counts since the previous scan · 1 newly fixed",
    );
  });

  it("is null for an admission or a scan without a readable diff", () => {
    expect(scanDiffSummary(scan(), [])).toBeNull();
    expect(scanDiffSummary(scan({ result: { diff: { new: "x" } } }), [])).toBeNull();
  });
});

describe("scanTagDrift", () => {
  it("names the digest the tag moved to", () => {
    const s = scan({
      result: { tag_drift: { current_digest: "sha256:" + "b".repeat(64), drifted: true } },
    });
    expect(scanTagDrift(s)).toBe("tag moved to sha256:bbbbbbbbbbbb");
  });

  it("says the tag was not checked when the registry did not answer", () => {
    const s = scan({ result: { tag_drift: { current_digest: null, drifted: false } } });
    expect(scanTagDrift(s)).toBe("tag not checked: the registry did not answer");
  });

  it("says nothing when the tag did not move or no check ran", () => {
    const same = scan({
      result: { tag_drift: { current_digest: "sha256:" + "a".repeat(64), drifted: false } },
    });
    expect(scanTagDrift(same)).toBeNull();
    expect(scanTagDrift(scan({ result: { tag_drift: null } }))).toBeNull();
    expect(scanTagDrift(scan())).toBeNull();
  });
});
