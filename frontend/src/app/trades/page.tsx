'use client';
import React, { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTrades, useTradeStats } from '@/hooks/useTrades';
import { useSocket } from '@/hooks/useSocket';
import { TradeLogTable } from '@/components/trades/TradeLogTable';

export default function TradesPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const limit = 20;

  const { data: trades = [], isLoading } = useTrades({ page, limit });
  const { data: stats } = useTradeStats();
  const { latestTrade } = useSocket();

  // WS trade copy trigger refresh
  useEffect(() => {
    if (latestTrade) {
      queryClient.invalidateQueries({ queryKey: ['trades'] });
      queryClient.invalidateQueries({ queryKey: ['trade-stats'] });
    }
  }, [latestTrade, queryClient]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-bg-border/50 pb-4 select-none">
        <div>
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Historical Audit Log</h2>
        </div>
        
        {/* Quick stats */}
        <div className="flex gap-4 text-xs font-bold font-mono">
          <div className="bg-bg-panel border border-bg-border px-3 py-1.5 rounded-lg flex items-center gap-2">
            <span className="text-text-secondary">Avg Latency:</span>
            <span className="text-blue-400">{stats?.avg_execution_time_ms ? `${stats.avg_execution_time_ms}ms` : '-'}</span>
          </div>
          <div className="bg-bg-panel border border-bg-border px-3 py-1.5 rounded-lg flex items-center gap-2">
            <span className="text-text-secondary">Avg Slippage:</span>
            <span className="text-emerald-400">{stats?.avg_slippage_pct ? `${(stats.avg_slippage_pct * 100).toFixed(4)}%` : '-'}</span>
          </div>
        </div>
      </div>

      {/* Trade log table */}
      <TradeLogTable 
        trades={trades} 
        isLoading={isLoading} 
        page={page} 
        setPage={setPage} 
        hasMore={trades.length === limit} 
      />
    </div>
  );
}
