'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, pnlClass } from '@/components/admin/AdminUI';
import { Crown, ChevronDown, ArrowUpRight, ArrowDownRight } from 'lucide-react';

export default function AdminPositions() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.admin.positions();
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
            {/* User header */}
            <button onClick={() => toggle(u.id)}
              className="w-full flex items-center justify-between px-4 py-3 border-b border-bg-border hover:bg-bg-panel/40 transition-colors">
              <div className="flex items-center gap-2.5 min-w-0">
                <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${isOpen(u.id) ? '' : '-rotate-90'}`} />
                <span className="font-semibold text-text-primary truncate">{u.email}</span>
              </div>
              <span className="text-[11px] text-text-muted">
                {u.accounts.length} account{u.accounts.length === 1 ? '' : 's'} · {u.total_positions} position{u.total_positions === 1 ? '' : 's'}
              </span>
            </button>

            {isOpen(u.id) && (
              <div className="p-4 space-y-4">
                {u.accounts.map((a: any) => <AccountCard key={a.id} acc={a} />)}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function AccountCard({ acc }: { acc: any }) {
  const activePnL = (acc.positions || []).reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);
  return (
    <div className="rounded-xl border border-bg-border overflow-hidden shadow-sm">
      {/* Header — avatar + crown, name, stats (matches the trader view) */}
      <div className="flex items-center justify-between border-b border-bg-border bg-bg-panel/40 px-5 py-3.5 gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <span className="relative flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500/25 to-emerald-500/20 ring-1 ring-bg-border text-sm font-bold text-text-primary shrink-0">
            {acc.name?.charAt(0)?.toUpperCase() || '?'}
            {acc.is_master && (
              <span className="absolute -top-1.5 -right-1.5 flex items-center justify-center w-4 h-4 rounded-full bg-amber-400 ring-2 ring-bg-panel shadow-sm rotate-[18deg]">
                <Crown className="w-2.5 h-2.5 text-[#1a1205]" fill="currentColor" />
              </span>
            )}
          </span>
          <h3 className="font-bold text-text-primary text-sm tracking-tight truncate">{acc.name}</h3>
          {acc.is_master && <span className={`w-2 h-2 rounded-full ${acc.live ? 'bg-emerald-400' : 'bg-text-muted'}`} title={acc.live ? 'Listener live' : 'Not live'} />}
        </div>
        <div className="flex items-center gap-5 font-mono text-xs font-bold">
          <Stat label="Today" value={acc.today_pnl} pnl />
          <Stat label="Active P&L" value={activePnL} pnl />
          <div className="flex items-center gap-1.5">
            <span className="text-text-muted text-[10px] uppercase font-semibold">Balance:</span>
            <span className="text-text-primary">{acc.balance != null ? `${Number(acc.balance).toFixed(2)}` : '-'}</span>
          </div>
          {acc.allocated_balance != null && (
            <div className="flex items-center gap-1.5">
              <span className="text-text-muted text-[10px] uppercase font-semibold">Alloc:</span>
              <span className="text-blue-400">{Number(acc.allocated_balance).toFixed(2)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Positions table (matches trader layout) */}
      <div className="p-5">
        <h4 className="text-[10px] font-bold text-text-muted uppercase tracking-wider mb-2">Open Positions ({acc.positions.length})</h4>
        {acc.positions.length === 0 ? (
          <div className="py-4 text-center text-text-muted text-xs border border-dashed border-bg-border rounded-lg">No active open positions.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
                  <th className="py-2">Symbol</th>
                  <th>Side</th>
                  <th className="text-right">Quantity</th>
                  <th className="text-right">Entry Price</th>
                  <th className="text-right">Current Price</th>
                  <th className="text-right">Unrealized PNL</th>
                  <th className="text-right">Stop Loss</th>
                  <th className="text-right pr-2">Take Profit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04] font-medium">
                {acc.positions.map((pos: any) => {
                  const isLong = String(pos.side).toLowerCase() === 'long';
                  const pnl = Number(pos.unrealized_pnl || 0);
                  return (
                    <tr key={pos.id} className="hover:bg-bg-secondary/10 transition-colors">
                      <td className="py-2.5 font-bold text-text-primary">{pos.symbol}</td>
                      <td>
                        <span className={`inline-flex items-center gap-1 pl-1 pr-2 py-0.5 rounded-full text-[10px] font-bold ring-1 ${isLong ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/25' : 'bg-rose-500/10 text-rose-400 ring-rose-500/25'}`}>
                          {isLong ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                          {String(pos.side).toUpperCase()}
                        </span>
                      </td>
                      <td className="text-right font-mono text-text-primary">{Number(pos.quantity).toFixed(0)}</td>
                      <td className="text-right font-mono text-text-primary">{Number(pos.entry_price).toFixed(2)}</td>
                      <td className="text-right font-mono text-text-primary">{Number(pos.current_price || pos.entry_price).toFixed(2)}</td>
                      <td className={`text-right font-mono ${pnlClass(pnl)}`}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}</td>
                      <td className="text-right font-mono text-text-muted">{pos.sl_price != null ? Number(pos.sl_price).toFixed(2) : '—'}</td>
                      <td className="text-right font-mono text-text-muted pr-2">{pos.tp_price != null ? Number(pos.tp_price).toFixed(2) : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, pnl }: { label: string; value: number; pnl?: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-text-muted text-[10px] uppercase font-semibold">{label}:</span>
      <span className={pnl ? pnlClass(Number(value)) : 'text-text-primary'}>
        {Number(value) >= 0 ? '+' : ''}{Number(value || 0).toFixed(2)}
      </span>
    </div>
  );
}
