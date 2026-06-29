'use client';
import React from 'react';
import { useResolveAlert } from '@/hooks/useAlerts';
import { AlertTriangle, Info, ShieldAlert, CheckCircle } from 'lucide-react';

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

export function AlertsFeed({ alerts = [], isLoading }: { alerts?: any[]; isLoading: boolean }) {
  const resolveAlert = useResolveAlert();

  if (isLoading) {
    return (
      <div className="space-y-3 select-none">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="card-premium p-5 h-20 animate-pulse flex flex-col justify-between">
            <div className="h-3 bg-bg-secondary rounded w-1/4"></div>
            <div className="h-3 bg-bg-secondary rounded w-3/4"></div>
          </div>
        ))}
      </div>
    );
  }

  const getAlertConfig = (level: string) => {
    const l = level?.toLowerCase();
    if (l === 'critical') return { bar: 'bg-red-500', chip: 'bg-red-500/10 text-red-400', icon: ShieldAlert, label: 'text-red-400' };
    if (l === 'error') return { bar: 'bg-orange-500', chip: 'bg-orange-500/10 text-orange-400', icon: AlertTriangle, label: 'text-orange-400' };
    if (l === 'warning') return { bar: 'bg-amber-500', chip: 'bg-amber-500/10 text-amber-400', icon: AlertTriangle, label: 'text-amber-400' };
    return { bar: 'bg-blue-500', chip: 'bg-blue-500/10 text-blue-400', icon: Info, label: 'text-blue-400' };
  };

  if (alerts.length === 0) {
    return (
      <div className="card-premium py-16 flex flex-col items-center text-center select-none">
        <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-emerald-500/10 mb-3">
          <CheckCircle className="w-6 h-6 text-emerald-400" />
        </div>
        <h3 className="text-sm font-bold text-text-primary">All clear</h3>
        <p className="text-xs text-text-muted mt-1 max-w-xs">
          No alerts right now. Anything that needs your attention will show up here.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {alerts.map((alert) => {
        const config = getAlertConfig(alert.level);
        const Icon = config.icon;
        const resolved = alert.is_resolved;

        return (
          <div
            key={alert.id}
            className={`card-premium relative overflow-hidden flex items-start gap-4 p-4 pl-5 transition-all duration-200 ${resolved ? 'opacity-50' : ''}`}
          >
            {/* Severity bar */}
            <span className={`absolute left-0 top-0 bottom-0 w-1 ${resolved ? 'bg-bg-border' : config.bar}`} />

            {/* Icon chip */}
            <span className={`flex items-center justify-center w-9 h-9 rounded-xl shrink-0 ${resolved ? 'bg-bg-secondary text-text-muted' : config.chip}`}>
              <Icon className="w-[18px] h-[18px]" />
            </span>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-[10px] font-bold uppercase tracking-wider ${resolved ? 'text-text-muted' : config.label}`}>
                  {alert.level}
                </span>
                {alert.account_name && (
                  <span className="text-[10px] font-semibold bg-bg-secondary border border-bg-border px-1.5 py-0.5 rounded text-text-secondary">
                    {alert.account_name}
                  </span>
                )}
                <span className="text-[10px] font-mono text-text-muted" title={new Date(alert.created_at).toLocaleString()}>
                  {relativeTime(alert.created_at)}
                </span>
                {resolved && (
                  <span className="text-[10px] font-semibold text-emerald-400/70 flex items-center gap-1">
                    <CheckCircle className="w-3 h-3" /> Resolved
                  </span>
                )}
              </div>
              <p className={`text-xs mt-1 leading-relaxed ${resolved ? 'text-text-muted' : 'text-text-primary'} font-medium`}>
                {alert.message}
              </p>
            </div>

            {!resolved && (
              <button
                onClick={() => resolveAlert.mutate(alert.id)}
                disabled={resolveAlert.isPending}
                className="px-2.5 py-1.5 bg-bg-secondary hover:bg-emerald-500/10 text-text-secondary hover:text-emerald-400 rounded-lg border border-bg-border hover:border-emerald-500/30 transition-all text-[10px] font-bold flex items-center gap-1 select-none shrink-0"
              >
                <CheckCircle className="w-3.5 h-3.5" />
                RESOLVE
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
