'use client';
import React, { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAlerts, useClearAlerts } from '@/hooks/useAlerts';
import { useSocket } from '@/hooks/useSocket';
import { AlertsFeed } from '@/components/alerts/AlertsFeed';
import { Trash2 } from 'lucide-react';
import { Select } from '@/components/shared/Select';

export default function AlertsPage() {
  const queryClient = useQueryClient();
  const [level, setLevel] = useState<string>('');
  
  const { data: alerts = [], isLoading } = useAlerts({ level: level || undefined });
  const clearAlerts = useClearAlerts();
  const { latestAlert } = useSocket();

  // WS alert event refresh
  useEffect(() => {
    if (latestAlert) {
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
    }
  }, [latestAlert, queryClient]);

  const resolvedAlerts = alerts.filter(a => a.is_resolved);
  const active = alerts.filter(a => !a.is_resolved);
  const countBy = (lvl: string) => active.filter(a => a.level?.toLowerCase() === lvl).length;

  const summary = [
    { label: 'Active', value: active.length, color: 'text-text-primary', ring: 'bg-bg-secondary' },
    { label: 'Critical', value: countBy('critical'), color: 'text-red-400', ring: 'bg-red-500/10' },
    { label: 'Error', value: countBy('error'), color: 'text-orange-400', ring: 'bg-orange-500/10' },
    { label: 'Warning', value: countBy('warning'), color: 'text-amber-400', ring: 'bg-amber-500/10' },
    { label: 'Resolved', value: resolvedAlerts.length, color: 'text-emerald-400', ring: 'bg-emerald-500/10' },
  ];

  return (
    <div className="space-y-6">
      {/* Summary chips */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 select-none">
        {summary.map((s) => (
          <div key={s.label} className="card-premium px-4 py-3 flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">{s.label}</span>
            <span className={`text-2xl font-bold font-mono ${s.color}`}>{s.value}</span>
          </div>
        ))}
      </div>

      {/* Header / controls */}
      <div className="flex items-center justify-between border-b border-bg-border/50 pb-4 select-none">
        <div className="flex items-center gap-4">
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Alert Feed</h2>
          <Select
            value={level}
            onChange={setLevel}
            size="sm"
            className="w-40"
            options={[
              { value: '', label: 'All Levels' },
              { value: 'info', label: 'Info' },
              { value: 'warning', label: 'Warning' },
              { value: 'error', label: 'Error' },
              { value: 'critical', label: 'Critical' },
            ]}
          />
        </div>

        {resolvedAlerts.length > 0 && (
          <button
            onClick={() => clearAlerts.mutate()}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-xs font-semibold rounded-lg transition-all"
          >
            <Trash2 className="w-3.5 h-3.5" />
            Clear Resolved ({resolvedAlerts.length})
          </button>
        )}
      </div>

      {/* Alerts Feed */}
      <AlertsFeed alerts={alerts} isLoading={isLoading} />
    </div>
  );
}
