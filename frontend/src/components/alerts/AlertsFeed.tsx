'use client';
import React from 'react';
import { useResolveAlert } from '@/hooks/useAlerts';
import { AlertTriangle, Info, ShieldAlert, CheckCircle } from 'lucide-react';

export function AlertsFeed({ alerts = [], isLoading }: { alerts?: any[]; isLoading: boolean }) {
  const resolveAlert = useResolveAlert();

  if (isLoading) {
    return (
      <div className="space-y-4 select-none">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="card-premium p-5 h-24 animate-pulse flex flex-col justify-between">
            <div className="h-4 bg-bg-secondary rounded w-1/4"></div>
            <div className="h-4 bg-bg-secondary rounded w-3/4"></div>
          </div>
        ))}
      </div>
    );
  }

  const getAlertConfig = (level: string) => {
    const l = level?.toLowerCase();
    if (l === 'critical') {
      return {
        styles: 'border-red-500/30 bg-red-500/5 text-red-400 glow-red',
        icon: ShieldAlert,
        color: 'text-red-400',
      };
    }
    if (l === 'error') {
      return {
        styles: 'border-orange-500/30 bg-orange-500/5 text-orange-400 glow-orange',
        icon: AlertTriangle,
        color: 'text-orange-400',
      };
    }
    if (l === 'warning') {
      return {
        styles: 'border-amber-500/30 bg-amber-500/5 text-amber-400 glow-yellow',
        icon: AlertTriangle,
        color: 'text-amber-400',
      };
    }
    return {
      styles: 'border-blue-500/30 bg-blue-500/5 text-blue-400 glow-blue',
      icon: Info,
      color: 'text-blue-400',
    };
  };

  return (
    <div className="space-y-4">
      {alerts.length === 0 ? (
        <div className="card-premium p-8 text-center text-text-muted text-xs select-none">
          No system alerts logged. Everything is running smoothly.
        </div>
      ) : (
        alerts.map((alert) => {
          const config = getAlertConfig(alert.level);
          const Icon = config.icon;
          const date = new Date(alert.created_at);
          const timeStr = date.toLocaleString();

          return (
            <div
              key={alert.id}
              className={`border rounded-xl p-5 transition-all duration-200 flex items-start justify-between gap-4 ${
                alert.is_resolved 
                  ? 'border-bg-border bg-bg-panel/30 text-text-muted opacity-60' 
                  : config.styles
              }`}
            >
              <div className="flex items-start gap-3">
                <Icon className={`w-5 h-5 mt-0.5 shrink-0 ${alert.is_resolved ? 'text-text-muted' : config.color}`} />
                <div className="space-y-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[10px] font-bold uppercase tracking-wider select-none">
                      {alert.level}
                    </span>
                    <span className="text-text-muted select-none text-[10px] font-mono">
                      • {timeStr}
                    </span>
                    {alert.account_name && (
                      <span className="text-[10px] font-semibold bg-bg-secondary border border-bg-border px-1.5 py-0.5 rounded text-text-secondary select-none">
                        ACCOUNT: {alert.account_name}
                      </span>
                    )}
                  </div>
                  <p className={`text-xs ${alert.is_resolved ? 'text-text-muted line-through' : 'text-text-primary'} font-medium`}>
                    {alert.message}
                  </p>
                </div>
              </div>

              {!alert.is_resolved && (
                <button
                  onClick={() => resolveAlert.mutate(alert.id)}
                  disabled={resolveAlert.isPending}
                  className="px-2.5 py-1 bg-bg-secondary hover:bg-[#2e2e3e] text-text-secondary hover:text-emerald-400 rounded-lg border border-[#2e2e3e] hover:border-emerald-500/20 transition-all text-[10px] font-bold flex items-center gap-1 select-none shrink-0"
                >
                  <CheckCircle className="w-3.5 h-3.5" />
                  RESOLVE
                </button>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
