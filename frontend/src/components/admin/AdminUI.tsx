'use client';
import React from 'react';
import { RefreshCw } from 'lucide-react';

export function AdminHeader({ title, subtitle, onRefresh, refreshing, children }: {
  icon?: any; title: string; subtitle?: string; onRefresh?: () => void; refreshing?: boolean; children?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between border-b border-bg-border pb-4 mb-6 select-none">
      <div>
        <h1 className="text-base font-bold text-text-primary tracking-tight">{title}</h1>
        {subtitle && <p className="text-xs text-text-muted mt-0.5">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2">
        {children}
        {onRefresh && (
          <button onClick={onRefresh}
            className="flex items-center gap-2 text-xs font-semibold bg-bg-panel border border-bg-border hover:border-blue-500/50 text-text-secondary hover:text-text-primary px-3 py-1.5 rounded-lg transition-colors">
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} /> Refresh
          </button>
        )}
      </div>
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
