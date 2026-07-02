'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, StatusPill, pnlClass } from '@/components/admin/AdminUI';
import { Crown, User, Search } from 'lucide-react';

export default function AdminAccounts() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setAccounts(await api.admin.accounts()); setError('');
    } catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = accounts.filter(a => {
    if (!q) return true;
    const s = q.toLowerCase();
    return (a.name || '').toLowerCase().includes(s) || (a.owner_email || '').toLowerCase().includes(s);
  });

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading}>
        <div className="flex items-center gap-2 bg-bg-panel border border-bg-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-text-muted" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name / owner"
            className="bg-transparent outline-none text-xs text-text-primary placeholder:text-text-muted w-44" />
        </div>
      </AdminHeader>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      <div className="card-premium overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
                <th className="px-4 py-3 font-bold">Account</th>
                <th className="px-4 py-3 font-bold">Owner</th>
                <th className="px-4 py-3 font-bold">Role</th>
                <th className="px-4 py-3 font-bold">Status</th>
                <th className="px-4 py-3 font-bold">Env</th>
                <th className="px-4 py-3 font-bold text-right">Balance</th>
                <th className="px-4 py-3 font-bold text-right">Today PnL</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {filtered.map((a) => (
                <tr key={a.id} className="hover:bg-bg-panel/40 transition-colors">
                  <td className="px-4 py-3 font-medium text-text-primary">{a.name}</td>
                  <td className="px-4 py-3 text-text-secondary">{a.owner_email}</td>
                  <td className="px-4 py-3">
                    {a.is_master ? (
                      <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-amber-300">
                        <Crown className="w-3.5 h-3.5" /> Master
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-text-secondary">
                        <User className="w-3.5 h-3.5" /> Follower
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3"><StatusPill status={a.status} /></td>
                  <td className="px-4 py-3">
                    <span className={`text-[11px] font-semibold ${a.environment === 'live' ? 'text-emerald-400' : 'text-amber-400'}`}>{a.environment}</span>
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">
                    {a.balance != null ? Number(a.balance).toFixed(2) : '—'}
                    {a.allocated_balance != null && (
                      <span className="block text-[10px] text-text-muted">alloc {Number(a.allocated_balance).toFixed(0)}</span>
                    )}
                  </td>
                  <td className={`px-4 py-3 text-right font-mono font-semibold ${pnlClass(Number(a.today_pnl) || 0)}`}>
                    {a.today_pnl != null ? Number(a.today_pnl).toFixed(2) : '—'}
                  </td>
                </tr>
              ))}
              {!loading && filtered.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-10 text-center text-text-muted">No accounts found.</td></tr>
              )}
              {loading && filtered.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-10 text-center text-text-muted">
                  <span className="inline-flex items-center gap-2"><span className="inline-block w-4 h-4 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" /> Loading…</span>
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
