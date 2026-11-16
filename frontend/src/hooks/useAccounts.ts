'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';

export function useAccounts() {
  return useQuery({
    queryKey: ['accounts'],
    queryFn: api.accounts.list,
    refetchInterval: 30000,
  });
}


/** Check credentials before anything is created.
 *
 *  Not a mutation in the React Query sense — it writes nothing and nothing
 *  needs invalidating afterwards. It exists so the Add form can refuse to save
 *  a key that does not work, instead of saving it and leaving an account that
 *  looks fine on the list and only fails when a trade needs it.
 */
export async function verifyCredentials(data: {
  api_key: string; api_secret: string; environment: string;
}): Promise<{ ok: boolean; balance: number | null; message: string }> {
  return api.accounts.verify(data);
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
