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
      ring: 'bg-blue-500/10',
    },
    {
      title: 'Copy Success Rate',
      value: `${stats.success_rate_pct}%`,
      subtitle: 'Target: >99%',
      icon: CheckCircle2,
      color: stats.success_rate_pct >= 95 ? 'text-emerald-400' : 'text-rose-400',
      ring: stats.success_rate_pct >= 95 ? 'bg-emerald-500/10' : 'bg-rose-500/10',
    },
    {
      title: 'Avg. Slippage',
      value: `${(stats.avg_slippage_pct * 100).toFixed(4)}%`,
      subtitle: `Max: ${(stats.max_slippage_pct * 100).toFixed(4)}%`,
      icon: Zap,
      color: stats.avg_slippage_pct <= 0.0003 ? 'text-emerald-400' : 'text-rose-400',
      ring: stats.avg_slippage_pct <= 0.0003 ? 'bg-emerald-500/10' : 'bg-rose-500/10',
    },
    {
      title: 'Today\'s Total PnL',
      value: `${stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)} USDT`,
      subtitle: 'Across all followers',
      icon: TrendingUp,
      color: stats.total_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400',
      ring: stats.total_pnl >= 0 ? 'bg-emerald-500/10' : 'bg-rose-500/10',
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5 select-none">
      {cards.map((card, i) => {
        const Icon = card.icon;
        return (
          <div key={i} className="card-premium card-hover p-5 flex flex-col justify-between">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold text-text-muted uppercase tracking-[0.12em]">{card.title}</span>
              <span className={`flex items-center justify-center w-9 h-9 rounded-xl ${card.ring}`}>
                <Icon className={`w-[18px] h-[18px] ${card.color}`} />
              </span>
            </div>
            <div className="mt-5">
              <span className="text-[26px] leading-none font-bold tracking-tight text-text-primary font-mono">{card.value}</span>
              <p className="text-xs text-text-muted mt-2">{card.subtitle}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
