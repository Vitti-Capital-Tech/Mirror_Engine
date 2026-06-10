'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function useTrades(params?: Record<string, any>) {
  return useQuery({
    queryKey: ['trades', params],
    queryFn: () => api.trades.list(params),
    refetchInterval: 10000,
  });
}

export function useTradeStats() {
  return useQuery({
    queryKey: ['trade-stats'],
    queryFn: api.trades.stats,
    refetchInterval: 15000,
  });
}

export function useTradeDetails(id: string) {
  return useQuery({
    queryKey: ['trade-detail', id],
    queryFn: () => api.trades.getById(id),
    enabled: !!id,
  });
}
