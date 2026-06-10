'use client';
import React, { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions, useSyncStatus } from '@/hooks/usePositions';
import { useSocket } from '@/hooks/useSocket';
import { PositionsTable } from '@/components/positions/PositionsTable';

export default function PositionsPage() {
  const queryClient = useQueryClient();
  const { data: positions = [], isLoading } = usePositions();
  const { data: syncStatus } = useSyncStatus();
  const { latestPosition } = useSocket();

  // Refresh React Query data on WS position event
  useEffect(() => {
    if (latestPosition) {
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['positions-sync'] });
    }
  }, [latestPosition, queryClient]);

  const total = syncStatus?.total || 0;
  const synced = syncStatus?.synced || 0;
  const outOfSync = syncStatus?.out_of_sync || 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-bg-border/50 pb-4 select-none">
        <div>
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Positions Mirror Audit</h2>
        </div>
        
        {/* Sync Summary */}
        <div className="flex gap-4 text-xs font-bold font-mono">
          <div className="bg-bg-panel border border-bg-border px-3 py-1.5 rounded-lg flex items-center gap-2">
            <span className="text-text-secondary">Synced:</span>
            <span className="text-emerald-400">{synced} / {total}</span>
          </div>
          {outOfSync > 0 && (
            <div className="bg-red-500/10 border border-red-500/20 px-3 py-1.5 rounded-lg flex items-center gap-2 animate-pulse">
              <span className="text-red-400">Out of Sync:</span>
              <span className="text-red-400 font-extrabold">{outOfSync}</span>
            </div>
          )}
        </div>
      </div>

      {/* Positions Table */}
      <PositionsTable positions={positions} isLoading={isLoading} />
    </div>
  );
}
