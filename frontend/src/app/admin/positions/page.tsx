'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, pnlClass } from '@/components/admin/AdminUI';
import { Crown, User, ChevronDown } from 'lucide-react';

export default function AdminPositions() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.admin.positions();
      // Hide paused accounts; drop users left with no accounts.
      const cleaned = (res.users || [])
        .map((u: any) => ({ ...u, accounts: (u.accounts || []).filter((a: any) => a.status !== 'paused') }))
        .filter((u: any) => u.accounts.length > 0);
      setUsers(cleaned); setError('');
    } catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 12000); return () => clearInterval(id); }, [load]);

  const toggle = (id: string) => setOpen(o => ({ ...o, [id]: !(o[id] ?? true) }));
  const isOpen = (id: string) => open[id] ?? true; // expanded by default

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading} />

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {!loading && users.length === 0 && (
        <div className="card-premium p-10 text-center text-text-muted">No users found.</div>
      )}

      <div className="space-y-4">
        {users.map((u) => (
          <div key={u.id} className="card-premium overflow-hidden">
            {/* User header */}
            <button onClick={() => toggle(u.id)}
              className="w-full flex items-center justify-between px-4 py-3 border-b border-bg-border hover:bg-bg-panel/40 transition-colors">
              <div className="flex items-center gap-2.5 min-w-0">
                <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${isOpen(u.id) ? '' : '-rotate-90'}`} />
                <span className="font-semibold text-text-primary truncate">{u.email}</span>
                <span className="text-[11px] text-text-muted">
                  {u.accounts.length} account{u.accounts.length === 1 ? '' : 's'} · {u.total_positions} position{u.total_positions === 1 ? '' : 's'}
                </span>
              </div>
              <span className={`font-mono text-sm font-semibold ${pnlClass(u.total_upnl)}`}>
                {u.total_upnl >= 0 ? '+' : ''}{Number(u.total_upnl).toFixed(2)}
              </span>
            </button>

            {isOpen(u.id) && (
              <div className="divide-y divide-bg-border/50">
                {u.accounts.map((a: any) => (
                  <AccountBlock key={a.id} acc={a} />
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function AccountBlock({ acc }: { acc: any }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        {acc.is_master
          ? <Crown className="w-3.5 h-3.5 text-amber-400" />
          : <User className="w-3.5 h-3.5 text-text-muted" />}
        <span className="text-sm font-semibold text-text-primary">{acc.name}</span>
        <span className={`text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded ${acc.is_master ? 'bg-amber-500/12 text-amber-300' : 'bg-bg-panel text-text-muted'}`}>
          {acc.is_master ? 'Master' : 'Follower'}
        </span>
        {acc.is_master && <span className={`w-2 h-2 rounded-full ${acc.live ? 'bg-emerald-400' : 'bg-text-muted'}`} title={acc.live ? 'Listener live' : 'Not live'} />}
        <span className={`text-[10px] ${acc.status === 'active' ? 'text-emerald-400' : 'text-amber-400'}`}>{acc.status}</span>
      </div>

      {acc.positions.length === 0 ? (
        <div className="text-[11px] text-text-muted pl-5">No open positions</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-text-muted">
                <th className="py-1.5 pr-4 font-semibold">Symbol</th>
                <th className="py-1.5 pr-4 font-semibold">Side</th>
                <th className="py-1.5 pr-4 font-semibold text-right">Qty</th>
                <th className="py-1.5 pr-4 font-semibold text-right">Entry</th>
                <th className="py-1.5 pr-4 font-semibold text-right">Mark</th>
                <th className="py-1.5 pr-4 font-semibold text-right">uPnL</th>
              </tr>
            </thead>
            <tbody>
              {acc.positions.map((p: any) => (
                <tr key={p.id} className="border-t border-bg-border/40">
                  <td className="py-1.5 pr-4 font-medium text-text-primary">{p.symbol}</td>
                  <td className="py-1.5 pr-4">
                    <span className={`font-semibold ${p.side === 'long' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {p.side === 'long' ? 'LONG' : 'SHORT'}
                    </span>
                  </td>
                  <td className="py-1.5 pr-4 text-right font-mono text-text-secondary">{p.quantity}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-text-secondary">{Number(p.entry_price).toFixed(2)}</td>
                  <td className="py-1.5 pr-4 text-right font-mono text-text-secondary">{Number(p.current_price).toFixed(2)}</td>
                  <td className={`py-1.5 pr-4 text-right font-mono font-semibold ${pnlClass(Number(p.unrealized_pnl))}`}>
                    {Number(p.unrealized_pnl) >= 0 ? '+' : ''}{Number(p.unrealized_pnl).toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
