'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

/**
 * The comparison reads /v2/fills for the master and every follower, so it is by
 * far the most expensive call in the API. It is NOT on a short refetch interval
 * like the positions views: a day's fills don't change under you, and polling it
 * every ten seconds would hammer the exchange's rate limit on behalf of a page
 * nobody is watching. Today's window refreshes on a slow timer; a past day is
 * settled history and never refetches on its own.
 */
export function useComparison(params: { date?: string; owner_id?: string }, isToday: boolean) {
  return useQuery({
    queryKey: ['comparison', params],
    queryFn: () => api.comparison.get(params),
    refetchInterval: isToday ? 120_000 : false,
    refetchOnWindowFocus: isToday,
    staleTime: isToday ? 60_000 : Infinity,
  });
}

export function useReportStatus(enabled: boolean) {
  return useQuery({
    queryKey: ['comparison-report-status'],
    queryFn: api.comparison.reportStatus,
    enabled,
    refetchInterval: 60_000,
  });
}
