'use client';
import React, { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions, useMasterOpenOrders } from '@/hooks/usePositions';
import { useAccounts } from '@/hooks/useAccounts';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/lib/api';
import { RefreshCw, ArrowUpRight, ArrowDownRight } from 'lucide-react';

export default function PositionsPage() {
  const queryClient = useQueryClient();
  const { data: accounts = [] } = useAccounts();
  const { data: positions = [], isLoading: positionsLoading } = usePositions();
  const { data: masterOrders = [], isLoading: masterOrdersLoading } = useMasterOpenOrders();
  const { latestPosition } = useSocket();
  const [syncing, setSyncing] = useState(false);

  // Refresh React Query data on WS position event
  useEffect(() => {
    if (latestPosition) {
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
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
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
      queryClient.invalidateQueries({ queryKey: ['master-open-orders'] });
    } catch (e) {
      console.error('Failed to sync live positions:', e);
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="space-y-8 select-none">
      {/* Header action button */}
      <div className="flex items-center justify-end">
        <button
          onClick={handleSyncLive}
          disabled={syncing}
          className="group relative inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold text-white overflow-hidden border border-blue-400/30 bg-gradient-to-b from-blue-500 to-blue-600 hover:from-blue-400 hover:to-blue-500 shadow-lg shadow-blue-500/25 transition-all disabled:opacity-70 disabled:cursor-not-allowed"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : 'group-hover:rotate-90 transition-transform duration-300'}`} />
          {syncing ? 'Syncing…' : 'Sync Now'}
        </button>
      </div>

      {/* Grouped account cards: master on top, active followers below.
          Paused accounts are intentionally hidden from the positions view. */}
      {(() => {
        const visible = accounts.filter((a: any) => a.status !== 'paused');
        const masterAccts = visible.filter((a: any) => a.is_master);
        const followerAccts = visible.filter((a: any) => !a.is_master);

        const renderCard = (acc: any) => {
          const accPositions = positions.filter((p: any) => p.account_id === acc.id);
          const accOrders = acc.is_master ? masterOrders : [];

          // Calculate active PnL (sum of position unrealized pnl values)
          const activePnL = accPositions.reduce((sum: number, p: any) => sum + Number(p.unrealized_pnl || 0), 0);

          return (
            <div key={acc.id} className="card-premium overflow-hidden shadow-md">
              {/* Card Header */}
              <div className="flex items-center justify-between border-b border-bg-border bg-bg-panel/40 px-6 py-4">
                <div className="flex items-center gap-3 min-w-0">
                  <span className="flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500/25 to-emerald-500/20 ring-1 ring-bg-border text-sm font-bold text-text-primary shrink-0">
                    {acc.name?.charAt(0)?.toUpperCase() || '?'}
                  </span>
                  <h3 className="font-bold text-text-primary text-sm tracking-tight leading-tight truncate">{acc.name}</h3>
                </div>
                
                <div className="flex items-center gap-6 font-mono text-xs font-bold">
                  <div className="flex items-center gap-2">
                    <span className="text-text-muted text-[10px] uppercase font-semibold">Active P&L:</span>
                    <span className={activePnL > 0 ? 'text-emerald-400' : activePnL < 0 ? 'text-red-400' : 'text-text-secondary'}>
                      {activePnL >= 0 ? '+' : ''}{activePnL.toFixed(2)} USDT
                    </span>
                  </div>
                  
                  <div className="flex items-center gap-2">
                    <span className="text-text-muted text-[10px] uppercase font-semibold">Balance:</span>
                    <span className="text-text-primary">
                      {acc.balance !== null ? `${Number(acc.balance).toFixed(2)} USDT` : '-'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Card Body */}
              <div className="p-6 space-y-6">
                
                {/* Positions list */}
                <div>
                  <h4 className="text-[10px] font-bold text-text-muted uppercase tracking-wider mb-2">Open Positions ({accPositions.length})</h4>
                  {accPositions.length === 0 ? (
                    <div className="py-4 text-center text-text-muted text-xs border border-dashed border-bg-border rounded-lg">
                      No active open positions.
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left text-xs border-collapse">
                        <thead>
                          <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
                            <th className="py-2">Symbol</th>
                            <th>Side</th>
                            <th className="text-right">Quantity</th>
                            <th className="text-right">Entry Price</th>
                            <th className="text-right">Current Price</th>
                            <th className="text-right">Unrealized PNL</th>
                            <th className="text-right pr-4">Opened At</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-bg-border/30 font-medium">
                          {accPositions.map((pos: any) => {
                            const isLong = pos.side?.toLowerCase() === 'long';
                            const pnl = Number(pos.unrealized_pnl || 0);
                            return (
                              <tr key={pos.id} className="hover:bg-bg-secondary/10 transition-colors">
                                <td className="py-2.5 font-bold text-text-primary">{pos.symbol}</td>
                                <td>
                                  <span className={`inline-flex items-center gap-1 pl-1 pr-2 py-0.5 rounded-full text-[10px] font-bold ring-1 ${
                                    isLong
                                      ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/25'
                                      : 'bg-rose-500/10 text-rose-400 ring-rose-500/25'
                                  }`}>
                                    {isLong ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                                    {pos.side?.toUpperCase()}
                                  </span>
                                </td>
                                <td className="text-right font-mono text-text-primary">{Number(pos.quantity).toFixed(0)}</td>
                                <td className="text-right font-mono text-text-primary">{Number(pos.entry_price).toFixed(2)}</td>
                                <td className="text-right font-mono text-text-primary">{Number(pos.current_price || pos.entry_price).toFixed(2)}</td>
                                <td className={`text-right font-mono ${pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                                  {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} USDT
                                </td>
                                <td className="text-right font-mono text-text-secondary pr-4">
                                  {pos.created_at
                                    ? (() => {
                                        const d = new Date(pos.created_at);
                                        return d.toLocaleString('en-IN', {
                                          day: '2-digit',
                                          month: 'short',
                                          hour: '2-digit',
                                          minute: '2-digit',
                                          hour12: true,
                                        });
                                      })()
                                    : '-'}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                {/* Open Orders list (only shown on Master or if follower has orders) */}
                {(acc.is_master || accOrders.length > 0) && (
                  <div>
                    <h4 className="text-[10px] font-bold text-text-muted uppercase tracking-wider mb-2">Open Orders ({accOrders.length})</h4>
                    {accOrders.length === 0 ? (
                      <div className="py-4 text-center text-text-muted text-xs border border-dashed border-bg-border rounded-lg">
                        No active open orders.
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left text-xs border-collapse">
                          <thead>
                            <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
                              <th className="py-2">Symbol</th>
                              <th>Side</th>
                              <th>Type</th>
                              <th className="text-right">Quantity</th>
                              <th className="text-right">Limit Price</th>
                              <th className="text-right pr-4">Trigger Price</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-bg-border/30 font-medium">
                            {accOrders.map((ord: any) => {
                              const isBuy = ord.side?.toLowerCase() === 'buy';
                              return (
                                <tr key={ord.id} className="hover:bg-bg-secondary/10 transition-colors">
                                  <td className="py-2.5 font-bold text-text-primary">{ord.symbol}</td>
                                  <td>
                                    <span className={`inline-flex items-center gap-1 pl-1 pr-2 py-0.5 rounded-full text-[10px] font-bold ring-1 ${
                                      isBuy
                                        ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/25'
                                        : 'bg-rose-500/10 text-rose-400 ring-rose-500/25'
                                    }`}>
                                      {isBuy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                                      {ord.side?.toUpperCase()}
                                    </span>
                                  </td>
                                  <td className="text-text-secondary uppercase">{ord.order_type || 'Limit'}</td>
                                  <td className="text-right font-mono text-text-primary">{Number(ord.quantity).toFixed(0)}</td>
                                  <td className="text-right font-mono text-text-primary">
                                    {ord.limit_price ? Number(ord.limit_price).toFixed(2) : '-'}
                                  </td>
                                  <td className="text-right font-mono text-text-secondary pr-4">
                                    {ord.stop_price ? Number(ord.stop_price).toFixed(2) : '-'}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        };

        return (
          <div className="space-y-10">
            {/* Master section */}
            <div className="space-y-3">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.15em]">Master Account</h3>
              {masterAccts.length > 0 ? (
                <div className="space-y-6">{masterAccts.map(renderCard)}</div>
              ) : (
                <div className="card-premium py-6 text-center text-text-muted text-xs">
                  No active master account.
                </div>
              )}
            </div>

            {/* Followers section */}
            <div className="space-y-3">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.15em]">
                Follower Accounts ({followerAccts.length})
              </h3>
              {followerAccts.length > 0 ? (
                <div className="space-y-6">{followerAccts.map(renderCard)}</div>
              ) : (
                <div className="card-premium py-6 text-center text-text-muted text-xs">
                  No active follower accounts.
                </div>
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
