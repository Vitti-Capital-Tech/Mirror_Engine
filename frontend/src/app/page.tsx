'use client';
import React, { useEffect } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { useSocket } from '@/hooks/useSocket';
import { StatsCards } from '@/components/dashboard/StatsCards';
import { SlippageChart } from '@/components/dashboard/SlippageChart';
import { RecentTrades } from '@/components/dashboard/RecentTrades';


export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { latestTrade, latestPosition, latestAlert } = useSocket();
  const [mounted, setMounted] = React.useState(false);

  // Queries
  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: api.dashboard.stats,
    refetchInterval: 30000,
  });

  const { data: trades = [], isLoading: tradesLoading } = useQuery({
    queryKey: ['trades', { limit: 10 }],
    queryFn: () => api.trades.list({ limit: 10 }),
    refetchInterval: 30000,
  });

  // Set mounted state
  useEffect(() => {
    setMounted(true);
  }, []);

  // Real-time invalidations on WS event triggers
  useEffect(() => {
    if (latestTrade) {
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
      queryClient.invalidateQueries({ queryKey: ['trades'] });
    }
  }, [latestTrade, queryClient]);

  useEffect(() => {
    if (latestPosition) {
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
      queryClient.invalidateQueries({ queryKey: ['positions'] });
    }
  }, [latestPosition, queryClient]);

  useEffect(() => {
    if (latestAlert) {
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] });
      queryClient.invalidateQueries({ queryKey: ['alerts'] });
    }
  }, [latestAlert, queryClient]);

  const todayStr = mounted
    ? new Date().toLocaleDateString(undefined, {
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric',
      })
    : '';

  return (
    <div className="space-y-6">
      {/* Date Header */}
      <div className="flex items-center justify-between select-none">
        <div>
          <h2 className="text-sm font-medium text-text-secondary">Terminal Control Panel</h2>
          <p className="text-xs text-text-muted mt-0.5">{todayStr}</p>
        </div>
      </div>

      {/* Stats Cards */}
      <StatsCards stats={stats} isLoading={statsLoading} />

      {/* Charts & Trades Grid */}
      <div className="w-full">
        <SlippageChart trades={trades} />
      </div>

      {/* Recent Trades Table */}
      <RecentTrades trades={trades} isLoading={tradesLoading} />
    </div>
  );
}
