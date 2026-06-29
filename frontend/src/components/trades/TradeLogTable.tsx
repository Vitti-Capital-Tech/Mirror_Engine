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
    const base = 'inline-flex items-center px-1.5 py-0.5 rounded text-[9px] font-bold';
    if (s === 'filled') return <span className={`${base} bg-emerald-500/10 text-emerald-400`}>FILLED</span>;
    if (s === 'skipped') return <span className={`${base} bg-slate-500/10 text-slate-300`}>SKIPPED</span>;
    if (s === 'retrying') return <span className={`${base} bg-blue-500/10 text-blue-400`}>RETRYING</span>;
    return <span className={`${base} bg-red-500/10 text-red-400`}>FAILED</span>;
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
              <tbody className="divide-y divide-white/[0.04] font-medium">
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
                        <td className="text-center">
                          <span
                            title={`${filledCopiesCount} of ${copies.length} follower${copies.length === 1 ? '' : 's'} filled`}
                            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-bold font-mono ${
                              copies.length === 0
                                ? 'bg-bg-secondary text-text-muted'
                                : filledCopiesCount === copies.length
                                  ? 'bg-emerald-500/10 text-emerald-400'
                                  : filledCopiesCount === 0
                                    ? 'bg-red-500/10 text-red-400'
                                    : 'bg-amber-500/10 text-amber-400'
                            }`}
                          >
                            {filledCopiesCount}/{copies.length}
                          </span>
                        </td>
                        <td className="text-center py-3.5">{getStatusBadge(trade.status)}</td>
                      </tr>
                      
                      {/* Expanded follower copy details */}
                      {isExpanded && (
                        <tr>
                          <td colSpan={9} className="bg-bg-primary/60 px-6 py-5 border-b border-white/[0.04]">
                            <div className="space-y-3">
                              <h4 className="text-[10px] text-text-muted font-bold uppercase tracking-[0.15em] select-none">
                                Follower Execution Breakdown
                              </h4>

                              {copies.length === 0 ? (
                                <div className="text-text-muted italic text-[11px] py-3 px-4 rounded-lg border border-dashed border-bg-border">
                                  No follower copies were triggered for this trade.
                                </div>
                              ) : (
                                <div className="rounded-xl border border-bg-border overflow-hidden">
                                  <table className="w-full text-left text-[11px] border-collapse">
                                    <thead>
                                      <tr className="text-text-muted uppercase font-bold text-[9px] bg-bg-secondary/60 select-none">
                                        <th className="py-2.5 px-4">Follower</th>
                                        <th className="text-right px-4">Exec Price</th>
                                        <th className="text-right px-4">Slippage</th>
                                        <th className="text-right px-4">Latency</th>
                                        <th className="px-4">Status</th>
                                        <th className="px-4">Notes</th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-white/[0.04] font-medium">
                                      {copies.map((copy: any, index: number) => {
                                        const slippagePct = Number(copy.slippage_pct || 0) * 100;
                                        const isHighSlippage = slippagePct > 0.03;

                                        return (
                                          <tr key={index} className="hover:bg-bg-secondary/30 transition-colors">
                                            <td className="py-2.5 px-4 text-text-primary font-bold">{copy.account_name}</td>
                                            <td className="text-right px-4 font-mono text-text-primary">
                                              {copy.execution_price ? Number(copy.execution_price).toFixed(2) : '-'}
                                            </td>
                                            <td className="text-right px-4 font-mono">
                                              {copy.status === 'filled' ? (
                                                <span className={`inline-flex items-center justify-end gap-1 ${isHighSlippage ? 'text-red-400 font-bold' : 'text-emerald-400'}`}>
                                                  {isHighSlippage && <AlertTriangle className="w-3 h-3" />}
                                                  {slippagePct.toFixed(4)}%
                                                </span>
                                              ) : (
                                                <span className="text-text-muted">-</span>
                                              )}
                                            </td>
                                            <td className="text-right px-4 font-mono text-text-secondary">
                                              {copy.execution_time_ms ? `${copy.execution_time_ms}ms` : '-'}
                                            </td>
                                            <td className="px-4 select-none">{getCopyStatusBadge(copy.status)}</td>
                                            <td className="px-4 text-text-muted text-[10px] max-w-[200px] truncate">
                                              {copy.failure_reason || '–'}
                                            </td>
                                          </tr>
                                        );
                                      })}
                                    </tbody>
                                  </table>
                                </div>
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
