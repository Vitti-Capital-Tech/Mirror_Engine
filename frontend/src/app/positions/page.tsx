'use client';
import React, { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions, useMasterOpenOrders } from '@/hooks/usePositions';
import { useAccounts } from '@/hooks/useAccounts';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/lib/api';

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
      <div className="flex justify-end border-b border-bg-border/50 pb-4">
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
      </div>

      {/* Account Cards Loop */}
      <div className="space-y-8">
        {accounts.map((acc: any) => {
          const accPositions = positions.filter((p: any) => p.account_id === acc.id);
          const accOrders = acc.is_master ? masterOrders : [];
          
          // Calculate active PnL (sum of position unrealized pnl values)
          const activePnL = accPositions.reduce((sum: number, p: any) => sum + Number(p.unrealized_pnl || 0), 0);
          
          return (
            <div key={acc.id} className="bg-bg-panel border border-bg-border rounded-xl overflow-hidden shadow-md">
              {/* Card Header */}
              <div className="flex items-center justify-between border-b border-bg-border bg-bg-panel/40 px-6 py-4">
                <div className="flex items-center gap-3">
                  <h3 className="font-bold text-text-primary text-sm uppercase tracking-wide">{acc.name}</h3>
                  <span className={`px-2 py-0.5 rounded text-[9px] font-bold ${
                    acc.is_master 
                      ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' 
                      : 'bg-purple-500/10 text-purple-400 border border-purple-500/20'
                  }`}>
                    {acc.is_master ? 'MASTER' : 'FOLLOWER'}
                  </span>
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
                                  <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                                    isLong 
                                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                                      : 'bg-red-500/10 text-red-400 border border-red-500/20'
                                  }`}>
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
                                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold ${
                                      isBuy 
                                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                                        : 'bg-red-500/10 text-red-400 border border-red-500/20'
                                    }`}>
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
        })}
      </div>
    </div>
  );
}
