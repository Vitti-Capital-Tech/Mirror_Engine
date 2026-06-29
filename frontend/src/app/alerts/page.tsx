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

  return (
    <div className="space-y-5">
      {/* Header / controls */}
      <div className="flex items-center justify-between select-none">
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold text-text-secondary">
            {active.length} active {active.length === 1 ? 'alert' : 'alerts'}
          </span>
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
