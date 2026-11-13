'use client';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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

/** Flatten one position from the dashboard.
 *
 *  The order is reduce-only and its size is read from the exchange server-side,
 *  so a screen that is a moment behind cannot over-close or flip a position.
 *  Closing a MASTER position does not close its followers here — the engine
 *  mirrors that exit through its normal path, and racing it from the UI would
 *  mean two closes for one intent.
 */
export function useClosePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, symbol }: { accountId: string; symbol: string }) =>
      api.positions.close(accountId, symbol),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['positions'] });
      qc.invalidateQueries({ queryKey: ['account-live-view'] });
    },
  });
}
