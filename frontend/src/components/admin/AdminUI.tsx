'use client';
import React from 'react';
import { RefreshCw } from 'lucide-react';

export function AdminHeader({ onRefresh, refreshing, children }: {
  icon?: any; title?: string; subtitle?: string; onRefresh?: () => void; refreshing?: boolean; children?: React.ReactNode;
}) {
  // The page title/subtitle live in the TopBar; this is just the actions row.
  if (!onRefresh && !children) return null;
  return (
    <div className="flex items-center justify-end gap-2 mb-5 select-none">
      {children}
      {onRefresh && (
        <button onClick={onRefresh}
          className="flex items-center gap-2 text-xs font-semibold bg-bg-panel border border-bg-border hover:border-blue-500/50 text-text-secondary hover:text-text-primary px-3 py-1.5 rounded-lg transition-colors">
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} /> Refresh
        </button>
      )}
    </div>
  );
}

export function StatCard({ label, value, icon: Icon, accent = 'bg-blue-500/15 text-blue-400', hint }: {
  label: string; value: React.ReactNode; icon: any; accent?: string; hint?: string;
}) {
  return (
    <div className="card-premium card-hover p-4 flex items-center gap-3.5">
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${accent}`}>
        <Icon className="w-5 h-5" />
      </div>
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wider text-text-muted font-semibold truncate">{label}</div>
        <div className="text-xl font-bold font-mono text-text-primary leading-tight">{value}</div>
        {hint && <div className="text-[11px] text-text-muted mt-0.5 truncate">{hint}</div>}
      </div>
    </div>
  );
}

export function RoleBadge({ role }: { role: string }) {
  return (
    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
      role === 'admin' ? 'bg-purple-500/15 text-purple-300 ring-1 ring-purple-500/20' : 'bg-bg-panel text-text-secondary ring-1 ring-bg-border'
    }`}>{role}</span>
  );
}

export function StatusPill({ status }: { status?: string }) {
  const map: Record<string, string> = {
    active: 'bg-emerald-500/12 text-emerald-300 ring-emerald-500/20',
    paused: 'bg-amber-500/12 text-amber-300 ring-amber-500/20',
    error: 'bg-red-500/12 text-red-300 ring-red-500/20',
    circuit_break: 'bg-red-500/12 text-red-300 ring-red-500/20',
  };
  const cls = map[status || ''] || 'bg-bg-panel text-text-secondary ring-bg-border';
  return <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ring-1 capitalize ${cls}`}>{status || '—'}</span>;
}

export function pnlClass(v: number) {
  return v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-text-secondary';
}

export function Loader({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="card-premium p-12 flex flex-col items-center justify-center gap-3">
      <div className="w-6 h-6 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
      <span className="text-xs text-text-muted">{label}</span>
    </div>
  );
}
