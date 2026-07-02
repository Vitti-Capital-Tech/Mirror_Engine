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
  const isOpen = (id: string) => open[id] ?? true;

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading} />

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {!loading && users.length === 0 && (
        <div className="card-premium p-10 text-center text-text-muted">No open positions.</div>
      )}

      <div className="space-y-4">
        {users.map((u) => (
          <div key={u.id} className="card-premium overflow-hidden">
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
              <div className="p-4 space-y-4">
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
    <div className="rounded-xl border border-bg-border overflow-hidden">
      {/* Account name row (crown for master, live dot) */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-bg-panel/50 border-b border-bg-border">
        {acc.is_master
          ? <Crown className="w-3.5 h-3.5 text-amber-400" />
          : <User className="w-3.5 h-3.5 text-text-muted" />}
        <span className="text-sm font-bold text-text-primary">{acc.name}</span>
        <span className={`text-[10px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded ${acc.is_master ? 'bg-amber-500/12 text-amber-300' : 'bg-bg-panel text-text-muted'}`}>
          {acc.is_master ? 'Master' : 'Follower'}
        </span>
        {acc.is_master && <span className={`w-2 h-2 rounded-full ${acc.live ? 'bg-emerald-400' : 'bg-text-muted'}`} title={acc.live ? 'Listener live' : 'Not live'} />}
      </div>

      {acc.positions.length === 0 ? (
        <div className="text-[11px] text-text-muted px-4 py-4 select-none">No open positions</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                <th className="py-2.5 pl-4">Symbol</th>
                <th>Side</th>
                <th className="text-right">Quantity</th>
                <th className="text-right">Entry Price</th>
                <th className="text-right">Current Price</th>
                <th className="text-right">Unrealized PNL</th>
                <th className="text-right">Stop Loss</th>
                <th className="text-right pr-4">Take Profit</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-bg-border/50 font-medium">
              {acc.positions.map((p: any) => {
                const isLong = String(p.side).toLowerCase() === 'long';
                const pnl = Number(p.unrealized_pnl || 0);
                return (
                  <tr key={p.id} className="hover:bg-bg-secondary/40 transition-colors">
                    <td className="py-3 pl-4 font-bold text-text-primary">{p.symbol}</td>
                    <td>
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${isLong ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
                        {isLong ? 'LONG' : 'SHORT'}
                      </span>
                    </td>
                    <td className="text-right font-mono text-text-secondary">{p.quantity}</td>
                    <td className="text-right font-mono text-text-secondary">{Number(p.entry_price).toFixed(2)}</td>
                    <td className="text-right font-mono text-text-secondary">{Number(p.current_price).toFixed(2)}</td>
                    <td className={`text-right font-mono font-semibold ${pnlClass(pnl)}`}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}</td>
                    <td className="text-right font-mono text-text-muted">{p.sl_price != null ? Number(p.sl_price).toFixed(2) : '—'}</td>
                    <td className="text-right font-mono text-text-muted pr-4">{p.tp_price != null ? Number(p.tp_price).toFixed(2) : '—'}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
