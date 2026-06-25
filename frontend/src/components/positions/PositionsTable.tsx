'use client';
import React from 'react';
import { SyncBadge } from '../shared/SyncBadge';

export function PositionsTable({ positions = [], isLoading }: { positions?: any[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="bg-bg-panel border border-bg-border rounded-xl p-6 h-64 animate-pulse flex flex-col justify-between">
        <div className="h-4 bg-bg-secondary rounded w-full mb-4"></div>
        <div className="flex-1 space-y-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="grid grid-cols-10 gap-4">
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-bg-panel border border-bg-border rounded-xl p-6">
      <div className="overflow-x-auto">
        {positions.length === 0 ? (
          <div className="py-12 text-center text-text-muted text-xs select-none">
            No active open positions on monitored accounts.
          </div>
        ) : (
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                <th className="py-3">Account</th>
                <th>Symbol</th>
                <th>Side</th>
                <th className="text-right">Quantity</th>
                <th className="text-right">Entry Price</th>
                <th className="text-right">Current Price</th>
                <th className="text-right">Unrealized PNL</th>
                <th className="text-right">Stop Loss</th>
                <th className="text-right">Take Profit</th>
                <th className="py-3 text-right pr-2">Last Synced</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-bg-border/50 font-medium">
              {positions.map((pos) => {
                const isOutOfSync = pos.sync_status === 'out_of_sync';
                const pnl = Number(pos.unrealized_pnl || 0);
                const isLong = pos.side?.toLowerCase() === 'long';

                return (
                  <tr 
                    key={pos.id} 
                    className={`hover:bg-bg-secondary/40 transition-colors ${
                      isOutOfSync ? 'border-l-2 border-red-500 bg-red-500/5' : ''
                    }`}
                  >
                    <td className="py-3.5 pl-2">
                      <div className="font-bold text-text-primary">{pos.account_name}</div>
                    </td>
                    <td className="font-bold text-text-primary">{pos.symbol}</td>
                    <td className="select-none">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        isLong 
                           ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                           : 'bg-red-500/10 text-red-400 border border-red-500/20'
                      }`}>
                        {pos.side?.toUpperCase()}
                      </span>
                    </td>
                    <td className="text-right font-mono text-text-primary">
                      {Number(pos.quantity).toFixed(0)}
                    </td>
                    <td className="text-right font-mono text-text-primary">
                      {Number(pos.entry_price).toFixed(2)}
                    </td>
                    <td className="text-right font-mono text-text-primary">
                      {Number(pos.current_price || pos.entry_price).toFixed(2)}
                    </td>
                    <td className={`text-right font-mono ${pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                      {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} USDT
                    </td>
                    <td className="text-right font-mono text-text-secondary">
                      {pos.sl_price ? Number(pos.sl_price).toFixed(2) : '-'}
                    </td>
                    <td className="text-right font-mono text-text-secondary">
                      {pos.tp_price ? Number(pos.tp_price).toFixed(2) : '-'}
                    </td>
                    <td className="text-right font-mono text-text-secondary pr-2">
                      {pos.last_synced_at ? new Date(pos.last_synced_at).toLocaleTimeString() : '-'}
                    </td>
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
