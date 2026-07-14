'use client';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function usePositions() {
  return useQuery({ queryKey: ['positions'], queryFn: api.positions.list, refetchInterval: 3000 });
}

export function useSyncStatus() {
  return useQuery({ queryKey: ['positions-sync'], queryFn: api.positions.syncStatus, refetchInterval: 3000 });
}

export function useMasterOpenOrders() {
  return useQuery({ queryKey: ['master-open-orders'], queryFn: api.positions.masterOpenOrders, refetchInterval: 3000 });
}

export function useMasterOrderHistory() {
  return useQuery({ queryKey: ['master-order-history'], queryFn: api.positions.masterOrderHistory, refetchInterval: 5000 });
}
