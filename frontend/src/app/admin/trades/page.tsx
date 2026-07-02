'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader } from '@/components/admin/AdminUI';
import { Search } from 'lucide-react';

function timeAgo(iso?: string) {
  if (!iso) return '—';
  const d = new Date(iso).getTime();
  const s = Math.floor((Date.now() - d) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function AdminTrades() {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');

  const load = useCallback(async () => {
    try { setLoading(true); setRows(await api.admin.trades()); setError(''); }
    catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = rows.filter(r => {
    if (!q) return true;
    const s = q.toLowerCase();
    return (r.symbol || '').toLowerCase().includes(s) || (r.owner_email || '').toLowerCase().includes(s);
  });

  const statusChip = (st: string) => {
    const map: Record<string, string> = {
      completed: 'bg-emerald-500/12 text-emerald-300', filled: 'bg-emerald-500/12 text-emerald-300',
      partial: 'bg-amber-500/12 text-amber-300', failed: 'bg-red-500/12 text-red-300', pending: 'bg-blue-500/12 text-blue-300',
    };
    return map[st] || 'bg-bg-panel text-text-secondary';
  };

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading}>
        <div className="flex items-center gap-2 bg-bg-panel border border-bg-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-text-muted" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search symbol / owner"
            className="bg-transparent outline-none text-xs text-text-primary placeholder:text-text-muted w-44" />
        </div>
      </AdminHeader>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      <div className="card-premium overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
                <th className="px-4 py-3 font-bold">Time</th>
                <th className="px-4 py-3 font-bold">Owner</th>
                <th className="px-4 py-3 font-bold">Symbol</th>
                <th className="px-4 py-3 font-bold">Side</th>
                <th className="px-4 py-3 font-bold">Type</th>
                <th className="px-4 py-3 font-bold text-right">Qty</th>
                <th className="px-4 py-3 font-bold text-right">Entry</th>
                <th className="px-4 py-3 font-bold text-right">Copies</th>
                <th className="px-4 py-3 font-bold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {filtered.map((r) => (
                <tr key={r.id} className="hover:bg-bg-panel/40 transition-colors">
                  <td className="px-4 py-3 text-text-muted whitespace-nowrap">{timeAgo(r.created_at)}</td>
                  <td className="px-4 py-3 text-text-secondary">{r.owner_email}</td>
                  <td className="px-4 py-3 font-medium text-text-primary">{r.symbol}</td>
                  <td className="px-4 py-3">
                    <span className={`font-semibold ${String(r.side).toLowerCase() === 'buy' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {String(r.side || '').toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3 capitalize text-text-secondary">{r.trade_type}</td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{r.quantity}</td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{r.entry_price != null ? Number(r.entry_price).toFixed(2) : '—'}</td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{r.copies_filled}/{r.copies_total}</td>
                  <td className="px-4 py-3">
                    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full capitalize ${statusChip(r.status)}`}>{r.status || '—'}</span>
                  </td>
                </tr>
              ))}
              {!loading && filtered.length === 0 && (
                <tr><td colSpan={9} className="px-4 py-10 text-center text-text-muted">No trades found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
