'use client';
import React from 'react';
import { Users, CheckCircle2, Zap, TrendingUp } from 'lucide-react';

interface Stats {
  total_accounts: number;
  active_accounts: number;
  paused_accounts: number;
  error_accounts: number;
  success_rate_pct: number;
  avg_slippage_pct: number;
  max_slippage_pct: number;
  total_pnl: number;
}

export function StatsCards({ stats, isLoading }: { stats?: Stats; isLoading: boolean }) {
  if (isLoading || !stats) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 select-none">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="bg-bg-panel border border-bg-border rounded-xl p-6 h-32 animate-pulse flex flex-col justify-between">
            <div className="h-4 bg-bg-secondary rounded w-1/3"></div>
            <div className="h-8 bg-bg-secondary rounded w-2/3 mt-2"></div>
          </div>
        ))}
      </div>
    );
  }

  const cards = [
    {
      title: 'Total Accounts',
      value: stats.total_accounts,
      subtitle: `${stats.active_accounts} active, ${stats.error_accounts} blocked`,
      icon: Users,
      color: 'text-blue-400',
      glow: 'shadow-blue-500/5',
    },
    {
      title: 'Copy Success Rate',
      value: `${stats.success_rate_pct}%`,
      subtitle: 'Target: >99%',
      icon: CheckCircle2,
      color: stats.success_rate_pct >= 95 ? 'text-emerald-400' : 'text-rose-400',
      glow: stats.success_rate_pct >= 95 ? 'shadow-emerald-500/5' : 'shadow-rose-500/5',
    },
    {
      title: 'Avg. Slippage',
      value: `${(stats.avg_slippage_pct * 100).toFixed(4)}%`,
      subtitle: `Max: ${(stats.max_slippage_pct * 100).toFixed(4)}%`,
      icon: Zap,
      color: stats.avg_slippage_pct <= 0.0003 ? 'text-emerald-400' : 'text-rose-400',
      glow: stats.avg_slippage_pct <= 0.0003 ? 'shadow-emerald-500/5' : 'shadow-rose-500/5',
    },
    {
      title: 'Today\'s Total PnL',
      value: `${stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)} USDT`,
      subtitle: 'Across all followers',
      icon: TrendingUp,
      color: stats.total_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400',
      glow: stats.total_pnl >= 0 ? 'shadow-emerald-500/5' : 'shadow-rose-500/5',
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 select-none">
      {cards.map((card, i) => {
        const Icon = card.icon;
        return (
          <div key={i} className={`bg-bg-panel border border-bg-border rounded-xl p-6 shadow-md ${card.glow} flex flex-col justify-between transition-all duration-200 hover:border-[#3b82f6]/30`}>
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-text-secondary uppercase tracking-wider">{card.title}</span>
              <Icon className={`w-5 h-5 ${card.color}`} />
            </div>
            <div className="mt-3">
              <span className={`text-2xl font-bold tracking-tight text-text-primary`}>{card.value}</span>
              <p className="text-xs text-text-muted mt-1">{card.subtitle}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
