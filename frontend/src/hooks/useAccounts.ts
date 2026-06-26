'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function useAccounts() {
  return useQuery({ queryKey: ['accounts'], queryFn: api.accounts.list, refetchInterval: 30000 });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.accounts.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => api.accounts.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function usePauseAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.accounts.pause,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useResumeAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.accounts.resume,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.accounts.delete,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function usePromoteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.accounts.promote,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}

export function useTestAccount() {
  return useMutation({ mutationFn: api.accounts.test });
}

export function useResetAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.accounts.reset,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['accounts'] }),
  });
}
