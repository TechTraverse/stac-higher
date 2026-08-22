/**
 * Client functions for the monitoring surfaces (M2-D, spec §7): alerts +
 * read state (M2-B/C routes), cross-collection flows, and notification
 * channels. Same-origin JSON; guard-shaped errors surface as
 * `MonitoringApiError` with `.status`/`.code`.
 */
import type { Association } from "@/lib/associations/types";

// Type-only server imports — fully erased at build time (the associations
// module uses the same pattern).
export type { ApiAlert as Alert, AlertState } from "@/lib/alerts/storage";
export type {
  ApiChannel as Channel,
  ChannelKind,
} from "@/lib/notifications/storage";
import type { ApiAlert } from "@/lib/alerts/storage";
import type { ApiChannel } from "@/lib/notifications/storage";

export class MonitoringApiError extends Error {
  code?: string;
  status: number;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "MonitoringApiError";
    this.status = status;
    this.code = code;
  }
}

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}) as Record<string, unknown>);
    const message =
      (typeof body.error === "string" && body.error) ||
      `Request failed: ${res.status}`;
    const code = typeof body.code === "string" ? body.code : undefined;
    throw new MonitoringApiError(message, res.status, code);
  }
  return res.json() as Promise<T>;
}

// -- alerts ------------------------------------------------------------------

export type AlertListState = "open" | "resolved";

/** One page of alerts is all the UI fetches; a full page means the list may
 * be truncated, so absence of an alert row proves nothing (the flows card's
 * on-time hint checks this before trusting a miss). */
export const ALERTS_PAGE_LIMIT = 100;

/** The flow monitor's alert kind for a blown §5.1 expectation, per direction.
 * Cross-runtime contract strings — the authoritative writer is MONITOR_KINDS
 * in services/pipeline/src/pipeline/flow/monitor.py; unit tests pin the
 * literals so a drift here fails the suite. */
export const EXPECTATION_BREACH_KIND = {
  ingest: "ingest_inactivity",
  deliver: "delivery_slo",
} as const;

export async function listAlerts(state: AlertListState): Promise<ApiAlert[]> {
  const data = await apiFetch<{ alerts: ApiAlert[] }>(
    `/api/alerts?state=${state}&limit=${ALERTS_PAGE_LIMIT}`,
  );
  return data.alerts;
}

export async function ackAlert(id: string): Promise<ApiAlert> {
  return apiFetch<ApiAlert>(`/api/alerts/${encodeURIComponent(id)}/ack`, {
    method: "POST",
  });
}

export async function resolveAlert(id: string): Promise<ApiAlert> {
  return apiFetch<ApiAlert>(`/api/alerts/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
  });
}

export async function getUnreadCount(): Promise<number> {
  const data = await apiFetch<{ unread: number }>("/api/alerts/unread");
  return data.unread;
}

export async function markAlertsRead(): Promise<void> {
  await apiFetch<{ last_read_at: string }>("/api/alerts/read", {
    method: "POST",
  });
}

// -- flows -------------------------------------------------------------------

export async function listFlows(): Promise<Association[]> {
  const data = await apiFetch<{ flows: Association[] }>("/api/monitoring/flows");
  return data.flows;
}

// -- notification channels ---------------------------------------------------

export interface ChannelCreatePayload {
  kind: "in_app" | "webhook";
  group_id: string;
  config?: { url?: string; secret?: string };
}

export async function listChannels(): Promise<ApiChannel[]> {
  const data = await apiFetch<{ channels: ApiChannel[] }>("/api/channels");
  return data.channels;
}

export async function createChannel(
  payload: ChannelCreatePayload,
): Promise<ApiChannel> {
  return apiFetch<ApiChannel>("/api/channels", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteChannel(id: string): Promise<void> {
  await apiFetch<{ deleted: boolean }>(
    `/api/channels/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}
