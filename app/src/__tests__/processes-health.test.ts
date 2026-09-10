import { describe, it, expect } from "vitest";
import { processVerdict } from "@/components/processes/health";
import { alertKindLabel } from "@/components/monitoring/shared";
import type { Alert } from "@/lib/monitoring/api";
import type { Process, ProcessRun } from "@/lib/processes/types";

const process = {
  id: "p1",
  enabled: true,
  current_revision: "r1",
} as unknown as Process;

function run(over: Partial<ProcessRun> = {}): ProcessRun {
  return {
    id: "run1", status: "succeeded", is_test: false, started_at: "2026-09-01T00:00:00Z",
    finished_at: "2026-09-01T00:01:00Z", rate_deferred_until: null, ...over,
  } as unknown as ProcessRun;
}

function alert(over: Partial<Alert> = {}): Alert {
  return {
    id: "a1", source: "flow", kind: "process_failed", connection_id: null, association_id: null,
    channel_id: null, state: "firing", message: "", first_seen: "", last_seen: "",
    acknowledged_at: null, acknowledged_by: null, resolved_at: null, group_id: null,
    connection_name: null, collection_id: null, process_id: "p1", source_id: null, ...over,
  } as Alert;
}

describe("processVerdict with alerts (I-84)", () => {
  it("is ledger-only when no alert list is given — unchanged behaviour", () => {
    expect(processVerdict(process, [run()], 1).health).toBe("ok");
  });

  it("a firing process alert is an error, labelled by its kind, even with a clean ledger", () => {
    const v = processVerdict(process, [run()], 1, [alert()]);
    expect(v.health).toBe("error");
    expect(v.label).toBe("Failing");
    // The kind label is friendly text ("process runs dead-lettered"), not the
    // raw enum value — match the same helper Task 2's overview test uses
    // rather than assuming a specific substring.
    expect(v.reason).toBe(alertKindLabel("process_failed"));
  });

  it("an acknowledged alert is a warning", () => {
    const v = processVerdict(process, [run()], 1, [alert({ state: "acknowledged" })]);
    expect(v.health).toBe("warn");
    expect(v.reason).toMatch(/acknowledged/);
  });

  it("ignores alerts for other processes", () => {
    expect(processVerdict(process, [run()], 1, [alert({ process_id: "p2" })]).health).toBe("ok");
  });

  it("deployment state still comes first — a disabled process is unknown even when alerting", () => {
    const disabled = { ...process, enabled: false } as Process;
    expect(processVerdict(disabled, [run()], 1, [alert()]).health).toBe("unknown");
  });
});
