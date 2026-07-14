'use client';
import React, { useState } from 'react';
import { ArrowUpRight, ArrowDownRight, Crown } from 'lucide-react';

function fmtTime(iso?: string) {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('en-IN', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
  });
}

const pnlCls = (v: number) => (v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-text-secondary');

export function AccountPositionCard({ acc, positions = [], orders = [], history = [], fills = [], risk = [] }: {
  acc: any; positions?: any[]; orders?: any[]; history?: any[]; fills?: any[]; risk?: any[];
}) {
  const activePnL = positions.reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);

  // Split resting orders into plain limit (Open Orders) vs stop/SL/TP (Stop Orders).
  const openOrders = orders.filter((o: any) => !o.stop_price);
  const stopOrders = orders.filter((o: any) => o.stop_price);

  // Delta-style tabs. Followers only get Positions (no order feeds fetched).
  const tabs = acc.is_master
    ? [
        { key: 'positions', label: `Positions (${positions.length})` },
        { key: 'open', label: `Open Orders (${openOrders.length})` },
        { key: 'stop', label: `Stop Orders (${stopOrders.length})` },
        { key: 'fills', label: `Fills (${fills.length})` },
        { key: 'history', label: `Order History (${history.length})` },
        { key: 'risk', label: `Risk & Margin (${risk.length})` },
      ]
    : [{ key: 'positions', label: `Positions (${positions.length})` }];
  const [tab, setTab] = useState('positions');

  return (
    <div className="card-premium overflow-hidden shadow-md">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-bg-border bg-bg-panel/40 px-4 sm:px-6 py-4">
        <div className="flex items-center gap-3 min-w-0">
          <span className="relative flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500/25 to-emerald-500/20 ring-1 ring-bg-border text-sm font-bold text-text-primary shrink-0">
            {acc.name?.charAt(0)?.toUpperCase() || '?'}
            {acc.is_master && (
              <span className="absolute -top-1.5 -right-1.5 flex items-center justify-center w-4 h-4 rounded-full bg-amber-400 ring-2 ring-bg-panel shadow-sm rotate-[18deg]">
                <Crown className="w-2.5 h-2.5 text-[#1a1205]" fill="currentColor" />
              </span>
            )}
          </span>
          <h3 className="font-bold text-text-primary text-sm tracking-tight leading-tight truncate">{acc.name}</h3>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs font-bold">
          <div className="flex items-center gap-2">
            <span className="text-text-muted text-[10px] uppercase font-semibold">Today P&L:</span>
            <span className={pnlCls(Number(acc.today_pnl || 0))}>
              {Number(acc.today_pnl || 0) >= 0 ? '+' : ''}{Number(acc.today_pnl || 0).toFixed(2)} USDT
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-text-muted text-[10px] uppercase font-semibold">Active P&L:</span>
            <span className={pnlCls(activePnL)}>{activePnL >= 0 ? '+' : ''}{activePnL.toFixed(2)} USDT</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-text-muted text-[10px] uppercase font-semibold">Balance:</span>
            <span className="text-text-primary">{acc.balance != null ? `${Number(acc.balance).toFixed(2)} USDT` : '-'}</span>
          </div>
          {acc.allocated_balance != null && (
            <div className="flex items-center gap-2">
              <span className="text-text-muted text-[10px] uppercase font-semibold">Alloc:</span>
              <span className="text-blue-400">{Number(acc.allocated_balance).toFixed(2)} USDT</span>
            </div>
          )}
        </div>
      </div>

      {/* Tab bar (Delta-style, horizontal) */}
      <div className="flex items-stretch gap-1 border-b border-bg-border px-2 sm:px-4 overflow-x-auto">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`relative px-3 py-2.5 text-[11px] font-semibold whitespace-nowrap transition-colors ${
              tab === t.key ? 'text-text-primary' : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            {t.label}
            {tab === t.key && <span className="absolute left-2 right-2 -bottom-px h-0.5 rounded-full bg-blue-400" />}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="p-4 sm:p-6">
        {tab === 'positions' && <PositionsTab positions={positions} />}
        {tab === 'open' && <OpenOrdersTab orders={openOrders} />}
        {tab === 'stop' && <OpenOrdersTab orders={stopOrders} emptyText="No active stop orders." />}
        {tab === 'fills' && <FillsTab fills={fills} />}
        {tab === 'history' && <HistoryTab history={history} />}
        {tab === 'risk' && <RiskTab risk={risk} />}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="py-6 text-center text-text-muted text-xs border border-dashed border-bg-border rounded-lg">{text}</div>;
}

function PositionsTab({ positions }: { positions: any[] }) {
  if (positions.length === 0) return <Empty text="No active open positions." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
            <th className="py-2">Symbol</th><th>Side</th>
            <th className="text-right">Quantity</th><th className="text-right">Entry Price</th>
            <th className="text-right">Current Price</th><th className="text-right">Unrealized PNL</th>
            <th className="text-right pr-4">Opened At</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {positions.map((pos: any) => {
            const isLong = pos.side?.toLowerCase() === 'long';
            const pnl = Number(pos.unrealized_pnl || 0);
            return (
              <tr key={pos.id} className="hover:bg-bg-secondary/10 transition-colors">
                <td className="py-2.5 font-bold text-text-primary">{pos.symbol}</td>
                <td>
                  <span className={`inline-flex items-center gap-1 pl-1 pr-2 py-0.5 rounded-full text-[10px] font-bold ring-1 ${isLong ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/25' : 'bg-rose-500/10 text-rose-400 ring-rose-500/25'}`}>
                    {isLong ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}{pos.side?.toUpperCase()}
                  </span>
                </td>
                <td className="text-right font-mono text-text-primary">{Number(pos.quantity).toFixed(0)}</td>
                <td className="text-right font-mono text-text-primary">{Number(pos.entry_price).toFixed(2)}</td>
                <td className="text-right font-mono text-text-primary">{Number(pos.current_price || pos.entry_price).toFixed(2)}</td>
                <td className={`text-right font-mono ${pnlCls(pnl)}`}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} USDT</td>
                <td className="text-right font-mono text-text-secondary pr-4 whitespace-nowrap">{fmtTime(pos.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function OpenOrdersTab({ orders, emptyText = 'No active open orders.' }: { orders: any[]; emptyText?: string }) {
  if (orders.length === 0) return <Empty text={emptyText} />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
            <th className="py-2">Symbol</th><th>Side</th><th>Type</th>
            <th className="text-right">Quantity</th><th className="text-right">Limit Price</th>
            <th className="text-right">Trigger Price</th><th className="text-right pr-4">Trigger Index</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {orders.map((ord: any) => {
            const isBuy = ord.side?.toLowerCase() === 'buy';
            const tm = (ord.trigger_method || '').toLowerCase();
            const triggerIndex = ord.stop_price
              ? (tm === 'mark_price' ? 'Mark Price' : tm === 'spot_price' ? 'Index Price' : tm === 'last_traded_price' ? 'Last Price' : tm ? tm.replace(/_/g, ' ') : '-')
              : '-';
            return (
              <tr key={ord.id} className="hover:bg-bg-secondary/10 transition-colors">
                <td className="py-2.5 font-bold text-text-primary">{ord.symbol}</td>
                <td>
                  <span className={`inline-flex items-center gap-1 pl-1 pr-2 py-0.5 rounded-full text-[10px] font-bold ring-1 ${isBuy ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/25' : 'bg-rose-500/10 text-rose-400 ring-rose-500/25'}`}>
                    {isBuy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}{ord.side?.toUpperCase()}
                  </span>
                </td>
                <td className="text-text-secondary uppercase">{ord.order_type || 'Limit'}</td>
                <td className="text-right font-mono text-text-primary">{Number(ord.quantity).toFixed(0)}</td>
                <td className="text-right font-mono text-text-primary">{ord.limit_price ? Number(ord.limit_price).toFixed(2) : '-'}</td>
                <td className="text-right font-mono text-text-secondary">{ord.stop_price ? Number(ord.stop_price).toFixed(2) : '-'}</td>
                <td className="text-right text-text-secondary pr-4 capitalize">{triggerIndex}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FillsTab({ fills }: { fills: any[] }) {
  if (fills.length === 0) return <Empty text="No recent fills." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
            <th className="py-2">Time</th><th>Symbol</th><th>Side</th>
            <th className="text-right">Size</th><th className="text-right">Price</th>
            <th>Role</th><th className="text-right pr-4">Commission</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {fills.map((f: any, i: number) => {
            const isBuy = f.side?.toLowerCase() === 'buy';
            return (
              <tr key={f.id ?? i} className="hover:bg-bg-secondary/10 transition-colors">
                <td className="py-2.5 font-mono text-text-secondary whitespace-nowrap">{fmtTime(f.created_at)}</td>
                <td className="font-bold text-text-primary">{f.symbol}</td>
                <td><span className={`font-bold ${isBuy ? 'text-emerald-400' : 'text-rose-400'}`}>{f.side?.toUpperCase()}</span></td>
                <td className="text-right font-mono text-text-primary">{Number(f.size).toFixed(0)}</td>
                <td className="text-right font-mono text-text-primary">{f.price != null ? Number(f.price).toFixed(2) : '-'}</td>
                <td className="capitalize text-text-secondary">{f.role || '-'}</td>
                <td className="text-right font-mono text-text-muted pr-4">{f.commission != null ? Number(f.commission).toFixed(4) : '-'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RiskTab({ risk }: { risk: any[] }) {
  if (risk.length === 0) return <Empty text="No open positions." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
            <th className="py-2">Symbol</th><th>Side</th>
            <th className="text-right">Size</th><th className="text-right">Entry</th>
            <th className="text-right">Mark</th><th className="text-right">Margin</th>
            <th className="text-right">Liq. Price</th><th className="text-right">Bankruptcy</th>
            <th className="text-right pr-4">ADL</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {risk.map((r: any, i: number) => {
            const isLong = r.side === 'long';
            return (
              <tr key={r.symbol ?? i} className="hover:bg-bg-secondary/10 transition-colors">
                <td className="py-2.5 font-bold text-text-primary">{r.symbol}</td>
                <td><span className={`font-bold ${isLong ? 'text-emerald-400' : 'text-rose-400'}`}>{(r.side || '').toUpperCase()}</span></td>
                <td className="text-right font-mono text-text-primary">{Number(r.size).toFixed(0)}</td>
                <td className="text-right font-mono text-text-secondary">{Number(r.entry_price).toFixed(2)}</td>
                <td className="text-right font-mono text-text-secondary">{r.mark_price != null ? Number(r.mark_price).toFixed(2) : '-'}</td>
                <td className="text-right font-mono text-text-primary">{r.margin != null ? Number(r.margin).toFixed(2) : '-'}</td>
                <td className="text-right font-mono text-amber-400">{r.liquidation_price != null ? Number(r.liquidation_price).toFixed(2) : '-'}</td>
                <td className="text-right font-mono text-text-muted">{r.bankruptcy_price != null ? Number(r.bankruptcy_price).toFixed(2) : '-'}</td>
                <td className="text-right font-mono text-text-muted pr-4">{r.adl_level ?? '-'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function HistoryTab({ history }: { history: any[] }) {
  if (history.length === 0) return <Empty text="No recent orders." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none">
            <th className="py-2">Time</th><th>Symbol</th><th>Side</th><th>Type</th>
            <th className="text-right">Qty</th><th className="text-right">Filled</th>
            <th className="text-right">Avg / Limit</th><th>Status</th><th className="pr-4">Reason</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {history.map((ord: any) => {
            const isBuy = ord.side?.toLowerCase() === 'buy';
            const price = ord.avg_fill_price ?? ord.limit_price;
            const st = (ord.state || '').toLowerCase();
            const stCls = st === 'closed' ? 'text-emerald-400' : st === 'cancelled' ? 'text-text-muted' : 'text-blue-400';
            return (
              <tr key={ord.id} className="hover:bg-bg-secondary/10 transition-colors">
                <td className="py-2.5 font-mono text-text-secondary whitespace-nowrap">{fmtTime(ord.created_at)}</td>
                <td className="font-bold text-text-primary">{ord.symbol}</td>
                <td><span className={`font-bold ${isBuy ? 'text-emerald-400' : 'text-rose-400'}`}>{ord.side?.toUpperCase()}</span></td>
                <td className="text-text-secondary uppercase">{ord.order_type || '-'}{ord.reduce_only ? ' · RO' : ''}</td>
                <td className="text-right font-mono text-text-primary">{Number(ord.quantity).toFixed(0)}</td>
                <td className="text-right font-mono text-text-secondary">{Number(ord.filled).toFixed(0)}</td>
                <td className="text-right font-mono text-text-primary">{price ? Number(price).toFixed(2) : '-'}</td>
                <td className={`capitalize font-semibold ${stCls}`}>{ord.state || '-'}</td>
                <td className="text-text-muted pr-4 capitalize whitespace-nowrap">{(ord.reason || '').replace(/_/g, ' ') || '-'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
