'use client';
import React from 'react';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';

export function RecentTrades({ trades = [], isLoading }: { trades?: any[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="bg-bg-panel border border-bg-border rounded-xl p-6 h-[400px] animate-pulse flex flex-col justify-between">
        <div className="h-4 bg-bg-secondary rounded w-1/4 mb-4"></div>
        <div className="flex-1 space-y-4">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="grid grid-cols-6 gap-4">
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const recent = trades.slice(0, 10);

  const getStatusBadge = (status: string) => {
    const s = status?.toLowerCase();
    if (s === 'copied') {
      return (
        <span className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded text-[10px] font-bold">
          COPIED
        </span>
      );
    }
    if (s === 'partial') {
      return (
        <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded text-[10px] font-bold">
          PARTIAL
        </span>
      );
    }
    return (
      <span className="bg-red-500/10 text-red-400 border border-red-500/20 px-2 py-0.5 rounded text-[10px] font-bold animate-pulse">
        FAILED
      </span>
    );
  };

  return (
    <div className="bg-bg-panel border border-bg-border rounded-xl p-6 flex flex-col h-[400px]">
      <div className="flex items-center justify-between mb-4 select-none">
        <h3 className="text-sm font-semibold text-text-primary">Recent Copy Executions</h3>
        <span className="text-[10px] text-text-muted font-bold uppercase tracking-wider">Last 10 trades</span>
      </div>

      <div className="flex-1 overflow-auto">
        {recent.length === 0 ? (
          <div className="w-full h-full flex items-center justify-center text-text-muted text-xs select-none">
            No copy executions recorded.
          </div>
        ) : (
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px]">
                <th className="py-2.5">Time</th>
                <th>Symbol</th>
                <th>Side</th>
                <th className="text-right">Master Price</th>
                <th className="text-center">Copies</th>
                <th className="text-right">Avg. Slippage</th>
                <th className="text-center py-2.5">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-bg-border/50 font-medium">
              {recent.map((trade) => {
                const date = new Date(trade.created_at);
                const timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                
                // Calculate average slippage
                const copies = trade.copies || [];
                const successfulCopies = copies.filter((c: any) => c.status === 'filled');
                const slippages = successfulCopies
                  .filter((c: any) => c.slippage_pct !== null)
                  .map((c: any) => Number(c.slippage_pct) * 100);
                
                const avgSlippage = slippages.length > 0
                  ? slippages.reduce((sum: number, val: number) => sum + val, 0) / slippages.length
                  : 0.0;

                const isBuy = trade.side?.toLowerCase() === 'buy';

                return (
                  <tr key={trade.id} className="hover:bg-bg-secondary/40 transition-colors">
                    <td className="py-3 text-text-secondary">{timeStr}</td>
                    <td className="font-bold text-text-primary">{trade.symbol}</td>
                    <td className="py-3">
                      <span className={`inline-flex items-center gap-0.5 ${isBuy ? 'text-emerald-400' : 'text-red-400'}`}>
                        {isBuy ? (
                          <>
                            <ArrowUpRight className="w-3.5 h-3.5" />
                            BUY
                          </>
                        ) : (
                          <>
                            <ArrowDownRight className="w-3.5 h-3.5" />
                            SELL
                          </>
                        )}
                      </span>
                    </td>
                    <td className="text-right text-text-primary font-mono">
                      {Number(trade.entry_price).toFixed(2)}
                    </td>
                    <td className="text-center text-text-secondary font-mono">
                      {successfulCopies.length} / {copies.length}
                    </td>
                    <td className={`text-right font-mono ${avgSlippage > 0.03 ? 'text-red-400' : 'text-emerald-400'}`}>
                      {avgSlippage.toFixed(4)}%
                    </td>
                    <td className="text-center py-3">{getStatusBadge(trade.status)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
