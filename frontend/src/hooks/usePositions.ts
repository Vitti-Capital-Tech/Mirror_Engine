'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function usePositions() {
  return useQuery({ queryKey: ['positions'], queryFn: api.positions.list, refetchInterval: 3000 });
}

export function useSyncStatus() {
  return useQuery({ queryKey: ['positions-sync'], queryFn: api.positions.syncStatus, refetchInterval: 3000 });
}

// Full live Delta view (orders / stop orders / fills / history / risk) for one
// account — powers the Delta-style tabs for master AND follower accounts.
export function useAccountLiveView(accountId: string, enabled = true) {
  return useQuery({
    queryKey: ['account-live-view', accountId],
    queryFn: () => api.positions.liveView(accountId),
    enabled: enabled && !!accountId,
    refetchInterval: 4000,
  });
}
