'use client';
import React from 'react';

export interface OpenOrder {
  id: string;
  symbol: string;
  side: string;
  quantity: number;
  limit_price: number | null;
  stop_price: number | null;
  order_type: string;
  created_at: string;
}

export function MasterOpenOrdersTable({ orders = [], isLoading }: { orders?: OpenOrder[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="bg-bg-panel border border-bg-border rounded-xl p-6 h-48 animate-pulse flex flex-col justify-between">
        <div className="h-4 bg-bg-secondary rounded w-full mb-4"></div>
        <div className="flex-1 space-y-4">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="grid grid-cols-6 gap-4">
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-bg-panel border border-bg-border rounded-xl p-6">
      <div className="overflow-x-auto">
        {orders.length === 0 ? (
          <div className="py-8 text-center text-text-muted text-xs select-none">
            No active open orders on the Master account.
          </div>
        ) : (
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                <th className="py-3">Symbol</th>
                <th>Side</th>
                <th>Type</th>
                <th className="text-right">Quantity</th>
                <th className="text-right">Limit Price</th>
                <th className="text-right">Trigger Price (SL/TP)</th>
                <th className="text-right py-3 pr-2">Created At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-bg-border/50 font-medium">
              {orders.map((order) => {
                const isBuy = order.side?.toLowerCase() === 'buy';
                const dateStr = order.created_at 
                  ? new Date(order.created_at).toLocaleTimeString() 
                  : '-';

                return (
                  <tr key={order.id} className="hover:bg-bg-secondary/40 transition-colors">
                    <td className="py-3 font-bold text-text-primary">{order.symbol}</td>
                    <td className="select-none">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        isBuy 
                          ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                          : 'bg-red-500/10 text-red-400 border border-red-500/20'
                      }`}>
                        {order.side?.toUpperCase()}
                      </span>
                    </td>
                    <td className="text-text-secondary uppercase text-[10px]">
                      {order.order_type?.replace('_', ' ')}
                    </td>
                    <td className="text-right font-mono text-text-primary">
                      {order.quantity}
                    </td>
                    <td className="text-right font-mono text-text-primary">
                      {order.limit_price !== null ? order.limit_price.toFixed(2) : '-'}
                    </td>
                    <td className="text-right font-mono text-amber-400">
                      {order.stop_price !== null ? order.stop_price.toFixed(2) : '-'}
                    </td>
                    <td className="text-right font-mono text-text-muted pr-2">
                      {dateStr}
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
