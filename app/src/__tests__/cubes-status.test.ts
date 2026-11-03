// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  CUBE_APPENDED_REASON_LABEL,
  CUBE_FAILED_REASON_LABEL,
  CUBE_SKIP_REASON_LABEL,
  cubeLedgerReasonLabel,
} from "@/lib/cubes/status";

describe("cubeLedgerReasonLabel", () => {
  it("labels a duplicate on an appended row", () => {
    expect(cubeLedgerReasonLabel("appended", "duplicate")).toBe(CUBE_APPENDED_REASON_LABEL.duplicate);
  });

  it("labels crash_loop and shows other failed text verbatim", () => {
    expect(cubeLedgerReasonLabel("failed", "crash_loop")).toBe(CUBE_FAILED_REASON_LABEL.crash_loop);
    expect(cubeLedgerReasonLabel("failed", "OSError: [Errno 13] denied")).toBe("OSError: [Errno 13] denied");
  });

  it("labels a skip reason and passes an unknown one through", () => {
    expect(cubeLedgerReasonLabel("skipped", "late")).toBe(CUBE_SKIP_REASON_LABEL.late);
    expect(cubeLedgerReasonLabel("skipped", "from_the_future")).toBe("from_the_future");
  });

  it("null reason is null", () => {
    expect(cubeLedgerReasonLabel("appended", null)).toBeNull();
  });

  it("a reason under the wrong status is not relabelled", () => {
    expect(cubeLedgerReasonLabel("skipped", "duplicate")).toBe("duplicate");
  });
});
