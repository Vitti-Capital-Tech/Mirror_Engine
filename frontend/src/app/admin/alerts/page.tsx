'use client';
import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { api } from '@/lib/api';
import { AdminHeader, Loader } from '@/components/admin/AdminUI';
import { AlertTriangle, ShieldAlert, Info, Search } from 'lucide-react';

function timeAgo(iso?: string) {
  if (!iso) return '—';
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const LEVELS = ['all', 'critical', 'error', 'warning', 'info'];

export default function AdminAlerts() {
  const [rows, setRows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [level, setLevel] = useState('all');
  const [q, setQ] = useState('');

  const load = useCallback(async () => {
    try { setLoading(true); setRows(await api.admin.alerts()); setError(''); }
    catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows.length };
    rows.forEach(r => { c[r.level] = (c[r.level] || 0) + 1; });
    return c;
  }, [rows]);

  const filtered = rows.filter(r => {
    if (level !== 'all' && r.level !== level) return false;
    if (!q) return true;
    const s = q.toLowerCase();
    return (r.message || '').toLowerCase().includes(s) || (r.owner_email || '').toLowerCase().includes(s) || (r.account_name || '').toLowerCase().includes(s);
  });

  const style = (lvl: string) => {
    const l = (lvl || '').toLowerCase();
    if (l === 'critical') return { Icon: ShieldAlert, ring: 'bg-red-500/12 text-red-400', bar: 'bg-red-500' };
    if (l === 'error') return { Icon: AlertTriangle, ring: 'bg-orange-500/12 text-orange-400', bar: 'bg-orange-500' };
    if (l === 'warning') return { Icon: AlertTriangle, ring: 'bg-amber-500/12 text-amber-400', bar: 'bg-amber-500' };
    return { Icon: Info, ring: 'bg-blue-500/12 text-blue-400', bar: 'bg-blue-500' };
  };

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading}>
        <div className="flex items-center gap-2 bg-bg-panel border border-bg-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-text-muted" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search message / owner"
            className="bg-transparent outline-none text-xs text-text-primary placeholder:text-text-muted w-48" />
        </div>
      </AdminHeader>

      {/* Level filter chips */}
      <div className="flex flex-wrap items-center gap-2 mb-5">
        {LEVELS.map(l => (
          <button key={l} onClick={() => setLevel(l)}
            className={`text-[11px] font-semibold px-3 py-1.5 rounded-lg border capitalize transition-colors ${
              level === l ? 'border-blue-500/50 bg-blue-500/10 text-text-primary' : 'border-bg-border bg-bg-panel text-text-secondary hover:text-text-primary'
            }`}>
            {l} {counts[l] ? <span className="text-text-muted">({counts[l]})</span> : null}
          </button>
        ))}
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      <div className="space-y-2">
        {filtered.map((a) => {
          const s = style(a.level);
          const Icon = s.Icon;
          return (
            <div key={a.id} className="card-premium relative overflow-hidden flex items-start gap-3 px-4 py-3">
              <span className={`absolute left-0 top-0 h-full w-1 ${s.bar}`} />
              <div className={`mt-0.5 w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${s.ring}`}>
                <Icon className="w-4 h-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-text-primary">{a.message}</span>
                  {a.is_resolved && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-emerald-500/12 text-emerald-300">resolved</span>}
                </div>
                <div className="flex items-center gap-2 text-[11px] text-text-muted mt-1">
                  <span className="capitalize">{a.type?.replace(/_/g, ' ')}</span>
                  <span>·</span>
                  <span className="text-text-secondary">{a.owner_email}</span>
                  <span>·</span>
                  <span>{a.account_name}</span>
                </div>
              </div>
              <span className="text-[11px] text-text-muted whitespace-nowrap shrink-0">{timeAgo(a.created_at)}</span>
            </div>
          );
        })}
        {loading && filtered.length === 0 && <Loader label="Loading alerts…" />}
        {!loading && filtered.length === 0 && (
          <div className="card-premium p-10 text-center text-text-muted">No alerts found.</div>
        )}
      </div>
    </div>
  );
}
