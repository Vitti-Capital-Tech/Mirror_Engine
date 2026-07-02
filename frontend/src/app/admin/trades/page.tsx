'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader } from '@/components/admin/AdminUI';
import { TradeLogTable } from '@/components/trades/TradeLogTable';
import { Search, ChevronDown } from 'lucide-react';

export default function AdminTrades() {
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    try { setLoading(true); const res = await api.admin.trades(); setUsers(res.users || []); setError(''); }
    catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const toggle = (id: string) => setOpen(o => ({ ...o, [id]: !(o[id] ?? true) }));
  const isOpen = (id: string) => open[id] ?? true;

  const filtered = users.filter(u => !q || (u.email || '').toLowerCase().includes(q.toLowerCase()));

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading}>
        <div className="flex items-center gap-2 bg-bg-panel border border-bg-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-text-muted" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search owner"
            className="bg-transparent outline-none text-xs text-text-primary placeholder:text-text-muted w-40" />
        </div>
      </AdminHeader>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {!loading && filtered.length === 0 && (
        <div className="card-premium p-10 text-center text-text-muted">No trades found.</div>
      )}

      <div className="space-y-4">
        {filtered.map((u) => (
          <div key={u.id} className="card-premium overflow-hidden">
            <button onClick={() => toggle(u.id)}
              className="w-full flex items-center justify-between px-4 py-3 border-b border-bg-border hover:bg-bg-panel/40 transition-colors">
              <div className="flex items-center gap-2.5 min-w-0">
                <ChevronDown className={`w-4 h-4 text-text-muted transition-transform ${isOpen(u.id) ? '' : '-rotate-90'}`} />
                <span className="font-semibold text-text-primary truncate">{u.email}</span>
              </div>
              <span className="text-[11px] text-text-muted">{u.count} trade{u.count === 1 ? '' : 's'}</span>
            </button>

            {isOpen(u.id) && (
              <div className="p-4">
                <TradeLogTable trades={u.trades} isLoading={false} page={1} setPage={() => {}} hasMore={false} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
