const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function buildQuery(params?: Record<string, any>): string {
  if (!params) return '';
  const clean: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') clean[k] = String(v);
  }
  const qs = new URLSearchParams(clean).toString();
  return qs ? `?${qs}` : '';
}

async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP Error ${res.status}`);
  }
  return res.json();
}

export const api = {
  accounts: {
    list: () => fetchAPI<any[]>('/api/accounts'),
    create: (data: any) => fetchAPI<any>('/api/accounts', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: string, data: any) => fetchAPI<any>(`/api/accounts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: string) => fetchAPI<any>(`/api/accounts/${id}`, { method: 'DELETE' }),
    pause: (id: string) => fetchAPI<any>(`/api/accounts/${id}/pause`, { method: 'POST' }),
    resume: (id: string) => fetchAPI<any>(`/api/accounts/${id}/resume`, { method: 'POST' }),
    promote: (id: string) => fetchAPI<any>(`/api/accounts/${id}/promote`, { method: 'POST' }),
    test: (id: string) => fetchAPI<any>(`/api/accounts/${id}/test`, { method: 'POST' }),
    reset: (id: string) => fetchAPI<any>(`/api/accounts/${id}/reset`, { method: 'POST' }),
  },
  trades: {
    list: (params?: Record<string, any>) => {
      const qs = buildQuery(params);
      return fetchAPI<any[]>(`/api/trades${qs}`);
    },
    getById: (id: string) => fetchAPI<any>(`/api/trades/${id}`),
    stats: () => fetchAPI<any>('/api/trades/stats'),
  },
  positions: {
    list: () => fetchAPI<any[]>('/api/positions'),
    byAccount: (accountId: string) => fetchAPI<any[]>(`/api/positions/${accountId}`),
    syncStatus: () => fetchAPI<any>('/api/positions/sync-status'),
    syncLive: () => fetchAPI<any>('/api/positions/sync-live', { method: 'POST' }),
    masterOpenOrders: () => fetchAPI<any[]>('/api/positions/master/open-orders'),
  },
  alerts: {
    list: (params?: Record<string, any>) => {
      const qs = buildQuery(params);
      return fetchAPI<any[]>(`/api/alerts${qs}`);
    },
    resolve: (id: string) => fetchAPI<any>(`/api/alerts/${id}/resolve`, { method: 'POST' }),
    clear: () => fetchAPI<any>('/api/alerts/clear', { method: 'DELETE' }),
  },
  dashboard: {
    stats: () => fetchAPI<any>('/api/dashboard/stats'),
    system: () => fetchAPI<any>('/api/dashboard/system'),
  },
};
