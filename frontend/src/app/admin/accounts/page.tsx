'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, StatusPill, pnlClass } from '@/components/admin/AdminUI';
import { Wallet, Crown, User, Search } from 'lucide-react';

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
      <AdminHeader icon={Wallet} title="All Accounts" subtitle="Every master and follower across all tenants" onRefresh={load} refreshing={loading}>
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
              <tr className="text-left text-[11px] uppercase tracking-wider text-text-muted border-b border-bg-border">
                <th className="px-4 py-3 font-semibold">Account</th>
                <th className="px-4 py-3 font-semibold">Owner</th>
                <th className="px-4 py-3 font-semibold">Role</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 font-semibold">Env</th>
                <th className="px-4 py-3 font-semibold text-right">Balance</th>
                <th className="px-4 py-3 font-semibold text-right">Today PnL</th>
                <th className="px-4 py-3 font-semibold">API Key</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((a) => (
                <tr key={a.id} className="border-b border-bg-border/50 hover:bg-bg-panel/40 transition-colors">
                  <td className="px-4 py-3 font-medium text-text-primary">{a.name}</td>
                  <td className="px-4 py-3 text-text-secondary">{a.owner_email}</td>
                  <td className="px-4 py-3">
                    {a.is_master ? (
                      <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-amber-300">
                        <Crown className="w-3.5 h-3.5" /> Master
                        <span className={`w-2 h-2 rounded-full ${a.live ? 'bg-emerald-400' : 'bg-text-muted'}`} title={a.live ? 'Listener live' : 'Not live'} />
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
                  <td className="px-4 py-3 font-mono text-[11px] text-text-muted">{a.api_key_hint}</td>
                </tr>
              ))}
              {!loading && filtered.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-text-muted">No accounts found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
