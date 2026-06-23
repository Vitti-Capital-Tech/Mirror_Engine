'use client';
import React, { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions, useSyncStatus, useMasterOpenOrders } from '@/hooks/usePositions';
import { useSocket } from '@/hooks/useSocket';
import { PositionsTable } from '@/components/positions/PositionsTable';
import { MasterOpenOrdersTable } from '@/components/positions/MasterOpenOrdersTable';
import { api } from '@/lib/api';

export default function PositionsPage() {
  const queryClient = useQueryClient();
  const { data: positions = [], isLoading } = usePositions();
  const { data: syncStatus } = useSyncStatus();
  const { data: masterOrders = [], isLoading: masterOrdersLoading } = useMasterOpenOrders();
  const { latestPosition } = useSocket();
  const [syncing, setSyncing] = useState(false);

  // Refresh React Query data on WS position event
  useEffect(() => {
    if (latestPosition) {
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['positions-sync'] });
    }
  }, [latestPosition, queryClient]);

  // Automatically sync positions from live exchange on initial page mount
  useEffect(() => {
    handleSyncLive();
  }, []);

  const handleSyncLive = async () => {
    try {
      setSyncing(true);
      await api.positions.syncLive();
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['positions-sync'] });
      queryClient.invalidateQueries({ queryKey: ['master-open-orders'] });
    } catch (e) {
      console.error('Failed to sync live positions:', e);
    } finally {
      setSyncing(false);
    }
  };

  const total = syncStatus?.total || 0;
  const synced = syncStatus?.synced || 0;
  const outOfSync = syncStatus?.out_of_sync || 0;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between border-b border-bg-border/50 pb-4 gap-4 select-none">
        <div>
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Positions Mirror Audit</h2>
          <p className="text-text-muted text-[10px] mt-0.5">Real-time status comparison between master and follower positions.</p>
        </div>
        
        {/* Actions & Sync Summary */}
        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleSyncLive}
            disabled={syncing}
            className={`px-3 py-1.5 bg-blue-500 hover:bg-blue-600 disabled:bg-blue-500/50 text-white text-xs font-semibold rounded-lg transition-all flex items-center gap-1.5 shadow-lg shadow-blue-500/10 ${syncing ? 'animate-pulse' : ''}`}
          >
            {syncing ? (
              <>
                <svg className="animate-spin h-3 w-3 text-white" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Syncing...
              </>
            ) : (
              <>
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 1121.21 7.89M9 11l3 3 8-8" />
                </svg>
                Sync Live Exchange Data
              </>
            )}
          </button>

          {outOfSync > 0 && (
            <div className="flex gap-2 text-xs font-bold font-mono">
              <div className="bg-red-500/10 border border-red-500/20 px-3 py-1.5 rounded-lg flex items-center gap-2 animate-pulse">
                <span className="text-red-400">Out of Sync:</span>
                <span className="text-red-400 font-extrabold">{outOfSync}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Positions Table Section */}
      <div className="space-y-3">
        <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-wider select-none">Open Positions</h3>
        <PositionsTable positions={positions} isLoading={isLoading} />
      </div>

      {/* Master Open Orders Section */}
      <div className="space-y-3">
        <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-wider select-none">Master Account Open Orders</h3>
        <MasterOpenOrdersTable orders={masterOrders} isLoading={masterOrdersLoading} />
      </div>
    </div>
  );
}
