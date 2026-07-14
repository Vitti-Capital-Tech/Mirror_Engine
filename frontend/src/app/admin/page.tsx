'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, pnlClass, Loader } from '@/components/admin/AdminUI';
import { ChevronDown } from 'lucide-react';
import { AccountPositionCard } from '@/components/positions/AccountPositionCard';

export default function AdminPositions() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const accounts = await api.admin.accounts();
      // Group active accounts by owner; master first within each owner.
      const byOwner: Record<string, any> = {};
      for (const a of accounts) {
        if (a.status === 'paused') continue;
        const key = a.owner_id || a.owner_email || 'unknown';
        const entry = byOwner[key] || (byOwner[key] = { id: key, email: a.owner_email || '—', accounts: [] });
        entry.accounts.push(a);
      }
      const grouped = Object.values(byOwner)
        .map((u: any) => ({
          ...u,
          accounts: u.accounts.sort((x: any, y: any) => (Number(!!y.is_master) - Number(!!x.is_master)) || String(x.name || '').localeCompare(String(y.name || ''))),
          today_pnl: u.accounts.reduce((s: number, a: any) => s + Number(a.today_pnl || 0), 0),
        }))
        .filter((u: any) => u.accounts.length > 0)
        .sort((a: any, b: any) => String(a.email || '').toLowerCase().localeCompare(String(b.email || '').toLowerCase()));
      setUsers(grouped); setError('');
    } catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 20000); return () => clearInterval(id); }, [load]);

  const toggle = (id: string) => setOpen(o => ({ ...o, [id]: !(o[id] ?? true) }));
  const isOpen = (id: string) => open[id] ?? true;

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading} />

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {loading && users.length === 0 && <Loader label="Loading positions…" />}
      {!loading && users.length === 0 && (
        <div className="card-premium p-10 text-center text-text-muted">No active accounts.</div>
      )}

      <div className="space-y-4">
        {users.map((u) => {
          const masters = u.accounts.filter((a: any) => a.is_master);
          const followers = u.accounts.filter((a: any) => !a.is_master);
          return (
            <div key={u.id} className="card-premium overflow-hidden">
              {/* User header */}
              <button onClick={() => toggle(u.id)}
                className="w-full flex items-center justify-between px-4 py-3 border-b border-bg-border hover:bg-bg-panel/40 transition-colors">
                <div className="flex items-center gap-2.5 min-w-0">
                  <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${isOpen(u.id) ? '' : '-rotate-90'}`} />
                  <span className="font-semibold text-text-primary truncate">{u.email}</span>
                </div>
                <div className="flex items-center gap-4 text-[11px] shrink-0">
                  <span className="text-text-muted">{u.accounts.length} account{u.accounts.length === 1 ? '' : 's'}</span>
                  <span className="font-mono font-bold text-text-muted">
                    Today: <span className={pnlClass(Number(u.today_pnl))}>{Number(u.today_pnl) >= 0 ? '+' : ''}{Number(u.today_pnl || 0).toFixed(2)}</span>
                  </span>
                </div>
              </button>

              {isOpen(u.id) && (
                <div className="p-4 space-y-6">
                  {masters.length > 0 && (
                    <div className="space-y-3">
                      <h4 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.15em]">Master Account</h4>
                      <div className="space-y-4">{masters.map((a: any) => <AccountPositionCard key={a.id} acc={a} />)}</div>
                    </div>
                  )}
                  {followers.length > 0 && (
                    <div className="space-y-3">
                      <h4 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.15em]">Follower Accounts ({followers.length})</h4>
                      <div className="space-y-4">{followers.map((a: any) => <AccountPositionCard key={a.id} acc={a} />)}</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
