'use client';
import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { api, getToken, setToken } from '@/lib/api';

interface User { id: string; email?: string; role?: string; }
interface AuthState {
  user: User | null;
  loading: boolean;
  signingOut: boolean;
  isAuthenticated: boolean;
  setSession: (accessToken: string, user?: User) => void;
  logout: () => void;
  refresh: () => Promise<void>;
}

const USER_KEY = 'me_user';

function readCachedUser(): User | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch { return null; }
}
function writeCachedUser(u: User | null) {
  if (typeof window === 'undefined') return;
  if (u) localStorage.setItem(USER_KEY, JSON.stringify(u));
  else localStorage.removeItem(USER_KEY);
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Hydrate synchronously from cache so role (admin/user) is known on first
  // paint after a refresh — avoids briefly showing the wrong panel.
  const [user, setUserState] = useState<User | null>(() => (getToken() ? readCachedUser() : null));
  const [loading, setLoading] = useState(true);
  const [signingOut, setSigningOut] = useState(false);

  const setUser = (u: User | null) => { setUserState(u); writeCachedUser(u); };

  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try {
      const me = await api.auth.me();
      setUser(me);
    } catch {
      setToken(null);
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const setSession = (accessToken: string, u?: User) => {
    setToken(accessToken);
    if (u) setUser(u);
    else refresh();
  };

  const logout = () => {
    setSigningOut(true);
    setToken(null);
    setUser(null);
    if (typeof window !== 'undefined') window.location.href = '/login';
  };

  return (
    <AuthContext.Provider value={{ user, loading, signingOut, isAuthenticated: !!user, setSession, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
