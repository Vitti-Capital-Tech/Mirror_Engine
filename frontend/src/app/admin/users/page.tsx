'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, RoleBadge, pnlClass } from '@/components/admin/AdminUI';
import { Crown, Search } from 'lucide-react';

export default function AdminUsers() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const ov = await api.admin.overview();
      setUsers(ov.users || []); setError('');
    } catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = users.filter(u => !q || (u.email || '').toLowerCase().includes(q.toLowerCase()));

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading}>
        <div className="flex items-center gap-2 bg-bg-panel border border-bg-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-text-muted" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search email"
            className="bg-transparent outline-none text-xs text-text-primary placeholder:text-text-muted w-40" />
        </div>
      </AdminHeader>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      <div className="card-premium overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
                <th className="px-4 py-3 font-bold">User</th>
                <th className="px-4 py-3 font-bold">Role</th>
                <th className="px-4 py-3 font-bold">Master</th>
                <th className="px-4 py-3 font-bold text-right">Followers</th>
                <th className="px-4 py-3 font-bold text-right">Active</th>
                <th className="px-4 py-3 font-bold text-right">Today PnL</th>
                <th className="px-4 py-3 font-bold text-right">Copies (Filled)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {filtered.map((u) => (
                <tr key={u.id} className="hover:bg-bg-panel/40 transition-colors">
                  <td className="px-4 py-3">
                    <div className="font-medium text-text-primary">{u.email || '—'}</div>
                    <div className="text-[11px] text-text-muted font-mono">{u.id.slice(0, 8)}…</div>
                  </td>
                  <td className="px-4 py-3"><RoleBadge role={u.role} /></td>
                  <td className="px-4 py-3">
                    {u.master_name ? (
                      <span className="flex items-center gap-1.5">
                        <Crown className="w-3.5 h-3.5 text-amber-400" />
                        <span className="text-text-primary">{u.master_name}</span>
                        <span className={`w-2 h-2 rounded-full ${u.master_live ? 'bg-emerald-400' : 'bg-text-muted'}`}
                          title={u.master_live ? 'Listener live' : 'Not live'} />
                      </span>
                    ) : <span className="text-text-muted">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{u.follower_count}</td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{u.active_accounts}/{u.total_accounts}</td>
                  <td className={`px-4 py-3 text-right font-mono font-semibold ${pnlClass(u.today_pnl)}`}>{Number(u.today_pnl).toFixed(2)}</td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{u.copies_filled_today}/{u.copies_today}</td>
                </tr>
              ))}
              {!loading && filtered.length === 0 && (
                <tr><td colSpan={7} className="px-4 py-10 text-center text-text-muted">No users found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
