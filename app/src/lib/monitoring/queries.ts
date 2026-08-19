/**
 * TanStack Query hooks for the monitoring surfaces (M2-D). Alerts and flows
 * poll (the pipeline reconciles once a minute; 30s keeps the page fresh
 * without hammering the DB-backed routes); the bell polls unread on the same
 * cadence from every page's Header.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { alertKeys, channelKeys, monitoringKeys } from "@/lib/query/keys";
import {
  ackAlert,
  createChannel,
  deleteChannel,
  getUnreadCount,
  listAlerts,
  listChannels,
  listFlows,
  markAlertsRead,
  resolveAlert,
  type AlertListState,
  type ChannelCreatePayload,
} from "./api";

const POLL_MS = 30_000;

export function useAlerts(state: AlertListState) {
  return useQuery({
    queryKey: alertKeys.list(state),
    queryFn: () => listAlerts(state),
    refetchInterval: POLL_MS,
  });
}

export function useUnreadAlerts() {
  return useQuery({
    queryKey: alertKeys.unread(),
    queryFn: getUnreadCount,
    refetchInterval: POLL_MS,
    retry: false,
  });
}

export function useAckAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ackAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: alertKeys.all() }),
  });
}

export function useResolveAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: resolveAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: alertKeys.all() }),
  });
}

export function useMarkAlertsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: markAlertsRead,
    onSuccess: () => qc.invalidateQueries({ queryKey: alertKeys.unread() }),
  });
}

export function useFlows() {
  return useQuery({
    queryKey: monitoringKeys.flows(),
    queryFn: listFlows,
    refetchInterval: POLL_MS,
  });
}

export function useChannels() {
  return useQuery({
    queryKey: channelKeys.list(),
    queryFn: listChannels,
  });
}

export function useCreateChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: ChannelCreatePayload) => createChannel(payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: channelKeys.all() }),
  });
}

export function useDeleteChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteChannel,
    onSuccess: () => qc.invalidateQueries({ queryKey: channelKeys.all() }),
  });
}
