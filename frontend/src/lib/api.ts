const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export const TOKEN_KEY = 'me_access_token';

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string | null) {
  if (typeof window === 'undefined') return;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

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
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options?.headers || {}),
    },
  });
  if (res.status === 401 && typeof window !== 'undefined' && !path.startsWith('/api/auth')) {
    // Session expired/invalid — drop token and bounce to login.
    setToken(null);
    if (!window.location.pathname.startsWith('/login')) window.location.href = '/login';
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP Error ${res.status}`);
  }
  return res.json();
}

export const api = {
  auth: {
    signup: (email: string, password: string) =>
      fetchAPI<any>('/api/auth/signup', { method: 'POST', body: JSON.stringify({ email, password }) }),
    login: (email: string, password: string) =>
      fetchAPI<any>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
    verify2fa: (pending_id: string, code: string) =>
      fetchAPI<any>('/api/auth/verify-2fa', { method: 'POST', body: JSON.stringify({ pending_id, code }) }),
    resend2fa: (pending_id: string) =>
      fetchAPI<any>('/api/auth/resend-2fa', { method: 'POST', body: JSON.stringify({ pending_id }) }),
    me: () => fetchAPI<any>('/api/auth/me'),
  },
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
    liveView: (accountId: string) =>
      fetchAPI<{ orders: any[]; history: any[]; fills: any[]; risk: any[] }>(`/api/positions/${accountId}/live-view`),
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
  admin: {
    overview: () => fetchAPI<any>('/api/admin/overview'),
    accounts: () => fetchAPI<any[]>('/api/admin/accounts'),
    positions: () => fetchAPI<any>('/api/admin/positions'),
    trades: () => fetchAPI<any>('/api/admin/trades'),
    alerts: () => fetchAPI<any[]>('/api/admin/alerts'),
    setRole: (userId: string, role: 'user' | 'admin') =>
      fetchAPI<any>(`/api/admin/users/${userId}/role?role=${role}`, { method: 'POST' }),
  },
};
