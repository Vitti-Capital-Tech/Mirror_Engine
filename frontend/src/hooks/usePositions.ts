'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function usePositions() {
  return useQuery({ queryKey: ['positions'], queryFn: api.positions.list, refetchInterval: 10000 });
}

export function useSyncStatus() {
  return useQuery({ queryKey: ['positions-sync'], queryFn: api.positions.syncStatus, refetchInterval: 10000 });
}

export function useMasterOpenOrders() {
  return useQuery({ queryKey: ['master-open-orders'], queryFn: api.positions.masterOpenOrders, refetchInterval: 10000 });
}
