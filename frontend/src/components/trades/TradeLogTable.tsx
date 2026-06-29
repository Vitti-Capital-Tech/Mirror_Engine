'use client';
import React, { useState } from 'react';
import { ArrowUpRight, ArrowDownRight, ChevronDown, ChevronUp, AlertTriangle } from 'lucide-react';

export function TradeLogTable({
  trades = [],
  isLoading,
  page,
  setPage,
  hasMore,
}: {
  trades: any[];
  isLoading: boolean;
  page: number;
  setPage: (p: number) => void;
  hasMore: boolean;
}) {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  const toggleRow = (id: string) => {
    const next = new Set(expandedRows);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setExpandedRows(next);
  };

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
      <span className="bg-red-500/10 text-red-400 border border-red-500/20 px-2 py-0.5 rounded text-[10px] font-bold">
        FAILED
      </span>
    );
  };

  const getCopyStatusBadge = (status: string) => {
    const s = status?.toLowerCase();
    if (s === 'filled') {
      return <span className="text-emerald-400 font-bold">FILLED</span>;
    }
    if (s === 'skipped') {
      return <span className="text-slate-400 font-bold">SKIPPED</span>;
    }
    if (s === 'retrying') {
      return <span className="text-blue-400 font-bold animate-pulse">RETRYING</span>;
    }
    return <span className="text-red-400 font-bold">FAILED</span>;
  };

  if (isLoading) {
    return (
      <div className="card-premium p-6 h-96 animate-pulse flex flex-col justify-between">
        <div className="h-4 bg-bg-secondary rounded w-full mb-4"></div>
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

  return (
    <div className="space-y-4">
      <div className="card-premium p-6">
        <div className="overflow-x-auto">
          {trades.length === 0 ? (
            <div className="py-12 text-center text-text-muted text-xs select-none">
              No trade logs found in the selected range.
            </div>
          ) : (
            <table className="w-full text-left text-xs border-collapse">
              <thead>
                <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                  <th className="py-3 pl-2"></th>
                  <th>Execution Time</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Type</th>
                  <th className="text-right">Quantity</th>
                  <th className="text-right">Master Price</th>
                  <th className="text-center">Copies Status</th>
                  <th className="text-center py-3">Chain Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-bg-border/50 font-medium">
                {trades.map((trade) => {
                  const date = new Date(trade.created_at);
                  const dateStr = date.toLocaleString();
                  const isBuy = trade.side?.toLowerCase() === 'buy';
                  const isExpanded = expandedRows.has(trade.id);
                  
                  const copies = trade.copies || [];
                  const filledCopiesCount = copies.filter((c: any) => c.status === 'filled').length;

                  return (
                    <React.Fragment key={trade.id}>
                      <tr 
                        onClick={() => toggleRow(trade.id)}
                        className="hover:bg-bg-secondary/40 transition-colors cursor-pointer select-none"
                      >
                        <td className="py-3.5 pl-2 text-text-muted">
                          {isExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                        </td>
                        <td className="py-3.5 text-text-secondary">{dateStr}</td>
                        <td className="font-bold text-text-primary">{trade.symbol}</td>
                        <td>
                          <span className={`inline-flex items-center gap-0.5 ${isBuy ? 'text-emerald-400' : 'text-red-400'}`}>
                            {isBuy ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                            {trade.side?.toUpperCase()}
                          </span>
                        </td>
                        <td className="text-text-secondary capitalize">{trade.trade_type?.replace('_', ' ')}</td>
                        <td className="text-right font-mono text-text-primary">{Number(trade.quantity).toFixed(0)}</td>
                        <td className="text-right font-mono text-text-primary">{Number(trade.entry_price).toFixed(2)}</td>
                        <td className="text-center text-text-secondary font-mono">
                          {filledCopiesCount} / {copies.length}
                        </td>
                        <td className="text-center py-3.5">{getStatusBadge(trade.status)}</td>
                      </tr>
                      
                      {/* Expanded follower copy details */}
                      {isExpanded && (
                        <tr>
                          <td colSpan={9} className="bg-[#0c0c12] p-4 border-t border-b border-bg-border/70">
                            <div className="space-y-3 pl-8">
                              <h4 className="text-[10px] text-blue-400 font-bold uppercase tracking-wider select-none">
                                Follower Execution Breakdown
                              </h4>
                              
                              {copies.length === 0 ? (
                                <div className="text-text-muted italic text-[11px] py-1">
                                  No follower copies were triggered for this trade.
                                </div>
                              ) : (
                                <table className="w-full text-left text-[11px] border-collapse bg-bg-secondary/70 border border-bg-border/80 rounded-lg overflow-hidden">
                                  <thead>
                                    <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] bg-bg-panel/35 select-none">
                                      <th className="py-2 pl-3">Follower Name</th>
                                      <th className="text-right">Execution Price</th>
                                      <th className="text-right">Slippage</th>
                                      <th className="text-right">Latency</th>
                                      <th>Status</th>
                                      <th className="py-2 pl-3">Notes / Failure Reason</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-bg-border/40 font-medium">
                                    {copies.map((copy: any, index: number) => {
                                      const slippagePct = Number(copy.slippage_pct || 0) * 100;
                                      const isHighSlippage = slippagePct > 0.03;
                                      
                                      return (
                                        <tr key={index} className="hover:bg-bg-panel/20 transition-colors">
                                          <td className="py-2 pl-3 text-text-primary font-bold">{copy.account_name}</td>
                                          <td className="text-right font-mono text-text-primary">
                                            {copy.execution_price ? Number(copy.execution_price).toFixed(2) : '-'}
                                          </td>
                                          <td className={`text-right font-mono ${
                                            copy.status !== 'filled' 
                                              ? 'text-text-muted' 
                                              : isHighSlippage 
                                                ? 'text-red-400 font-bold flex items-center justify-end gap-1' 
                                                : 'text-emerald-400'
                                          }`}>
                                            {copy.status === 'filled' ? (
                                              <>
                                                {isHighSlippage && <AlertTriangle className="w-3 h-3 text-red-400" />}
                                                {slippagePct.toFixed(4)}%
                                              </>
                                            ) : '-'}
                                          </td>
                                          <td className="text-right font-mono text-text-secondary">
                                            {copy.execution_time_ms ? `${copy.execution_time_ms}ms` : '-'}
                                          </td>
                                          <td className="select-none">{getCopyStatusBadge(copy.status)}</td>
                                          <td className="py-2 pl-3 text-red-400 text-[10px] max-w-[200px] truncate">
                                            {copy.failure_reason || '-'}
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Pagination controls */}
      {trades.length > 0 && (
        <div className="flex items-center justify-between px-4 select-none">
          <button
            onClick={() => setPage(Math.max(1, page - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-bg-panel border border-bg-border text-text-secondary hover:text-white rounded-lg disabled:opacity-30 disabled:hover:bg-bg-panel transition-all text-xs font-semibold"
          >
            Previous
          </button>
          <span className="text-xs text-text-muted font-bold font-mono">Page {page}</span>
          <button
            onClick={() => setPage(page + 1)}
            disabled={!hasMore}
            className="px-3 py-1.5 bg-bg-panel border border-bg-border text-text-secondary hover:text-white rounded-lg disabled:opacity-30 disabled:hover:bg-bg-panel transition-all text-xs font-semibold"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
