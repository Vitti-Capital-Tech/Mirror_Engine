'use client';
import React, { useState } from 'react';
import { ArrowUpRight, ArrowDownRight, Crown } from 'lucide-react';

// ── Formatting helpers (mirror the live Delta tables) ─────────────────────
const num = (v: any): number | null =>
  v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v);
const fmtNum = (v: any, d = 2) => {
  const n = num(v);
  return n == null ? '—' : n.toLocaleString(undefined, { maximumFractionDigits: d });
};
const fmtTs = (t?: string) => {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('en-IN', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
    });
  } catch { return '—'; }
};
const cap = (s: any) => (s ? String(s).replace(/^\w/, (c: string) => c.toUpperCase()) : '—');
const cleanType = (t: any) =>
  t ? String(t).replace('_order', '').replace(/^\w/, (c: string) => c.toUpperCase()) : '—';

const pnlCls = (v: number) => (v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-text-secondary');

// Left rail before the symbol: green for a long/buy leg, red for a short/sell leg.
function Rail({ sell }: { sell: boolean }) {
  return <span className={`inline-block w-[3px] h-3.5 rounded-full mr-2 align-middle ${sell ? 'bg-rose-500' : 'bg-emerald-500'}`} />;
}

const TH_ROW = 'text-text-muted border-b border-bg-border uppercase font-bold text-[9px] select-none';
const ROW = 'hover:bg-bg-secondary/10 transition-colors';

export function AccountPositionCard({ acc, positions = [], orders = [], history = [], fills = [], risk = [] }: {
  acc: any; positions?: any[]; orders?: any[]; history?: any[]; fills?: any[]; risk?: any[];
}) {
  const activePnL = positions.reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);

  // Split resting orders into plain limit (Open Orders) vs stop / SL / TP (Stop Orders).
  const openOrders = orders.filter((o: any) => !o.stop_price && !o.stop_order_type);
  const stopOrders = orders.filter((o: any) => o.stop_price || o.stop_order_type);

  // Delta-style tabs. Followers only get Positions (no order feeds fetched).
  const tabs = acc.is_master
    ? [
        { key: 'positions', label: `Positions (${risk.length})` },
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
        {tab === 'positions' && (
          acc.is_master
            ? <DeltaPositionsTab positions={risk} stopOrders={stopOrders} />
            : <PositionsTab positions={positions} />
        )}
        {tab === 'open' && <OpenOrdersTab orders={openOrders} />}
        {tab === 'stop' && <StopOrdersTab orders={stopOrders} />}
        {tab === 'fills' && <FillsTab fills={fills} />}
        {tab === 'history' && <HistoryTab history={history} />}
        {tab === 'risk' && <RiskTab positions={risk} wallet={acc.balance} />}
      </div>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div className="py-6 text-center text-text-muted text-xs border border-dashed border-bg-border rounded-lg">{text}</div>;
}

// TP / SL levels per symbol, derived from the resting stop orders.
function stopLevels(stopOrders: any[]) {
  const map: Record<string, { tp: number | null; sl: number | null }> = {};
  for (const so of stopOrders || []) {
    const sym = so.product_symbol;
    if (!sym) continue;
    const type = String(so.stop_order_type || '');
    const level = num(so.stop_price);
    if (!map[sym]) map[sym] = { tp: null, sl: null };
    if (type.includes('take_profit')) map[sym].tp = level;
    else if (type.includes('stop_loss')) map[sym].sl = level;
  }
  return map;
}

// ── Positions (live, master) — mirrors Delta's Positions tab ──────────────
function DeltaPositionsTab({ positions, stopOrders }: { positions: any[]; stopOrders: any[] }) {
  const open = (positions || []).filter((p) => Number(p.size) !== 0);
  if (open.length === 0) return <Empty text="No active open positions." />;
  const stopBySym = stopLevels(stopOrders);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse whitespace-nowrap">
        <thead>
          <tr className={TH_ROW}>
            <th className="py-2">Symbol</th><th className="text-right">Size</th><th className="text-right">Notional</th>
            <th className="text-right">Entry</th><th>TP / SL</th><th className="text-right">Index</th>
            <th className="text-right">Mark</th><th className="text-right">Margin</th>
            <th className="text-right">UPNL</th><th className="text-right pr-4">Cashflow</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {open.map((p, i) => {
            const size = num(p.size) || 0;
            const cv = num(p.product?.contract_value) ?? 0.001;
            const unit = p.product?.underlying_asset?.symbol || 'BTC';
            const long = size > 0;
            const btc = parseFloat((size * cv).toFixed(6));
            const idx = num(p.spot_price);
            const notional = idx != null ? Math.abs(size) * cv * idx : null;
            const entry = num(p.entry_price);
            const mark = num(p.mark_price);
            const pnl = (mark != null && entry != null) ? size * cv * (mark - entry) : (num(p.unrealized_pnl) ?? 0);
            const entryCost = entry != null ? Math.abs(size) * cv * entry : null;
            const pnlPct = entryCost && entryCost !== 0 ? (pnl / entryCost) * 100 : null;
            const margin = num(p.margin);
            const cashflow = num(p.realized_cashflow) ?? (entry != null ? -(size * cv * entry) : null);
            const st = stopBySym[p.product_symbol] || { tp: null, sl: null };
            return (
              <tr key={p.product_id ?? p.product_symbol ?? i} className={ROW}>
                <td className="py-2.5 font-bold text-text-primary"><Rail sell={!long} />{p.product_symbol || '—'}</td>
                <td className={`text-right font-mono font-bold ${long ? 'text-emerald-400' : 'text-rose-400'}`}>{long ? '+' : ''}{btc} {unit}</td>
                <td className="text-right font-mono text-text-primary">{notional != null ? `$${fmtNum(notional)}` : '—'}</td>
                <td className="text-right font-mono text-text-secondary">{fmtNum(entry)}</td>
                <td className="text-[11px]">
                  <span className="text-emerald-400">TP {st.tp != null ? fmtNum(st.tp) : '—'}</span>
                  <span className="text-text-muted"> · </span>
                  <span className="text-rose-400">SL {st.sl != null ? fmtNum(st.sl) : '—'}</span>
                </td>
                <td className="text-right font-mono text-text-secondary">{idx != null ? fmtNum(idx) : '—'}</td>
                <td className="text-right font-mono text-text-secondary">{fmtNum(mark)}</td>
                <td className="text-right font-mono text-text-primary">{margin != null && margin > 0 ? `$${fmtNum(margin)}` : '—'}</td>
                <td className={`text-right font-mono ${pnlCls(pnl)}`}>
                  {pnl >= 0 ? '+' : ''}{fmtNum(pnl)}
                  {pnlPct != null && <span className={`block text-[10px] ${pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{pnl >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</span>}
                </td>
                <td className={`text-right font-mono pr-4 ${cashflow == null ? 'text-text-muted' : pnlCls(cashflow)}`}>{cashflow != null ? `${cashflow >= 0 ? '+' : ''}${fmtNum(cashflow)}` : '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Positions (live, follower) — simple synced view ───────────────────────
function PositionsTab({ positions }: { positions: any[] }) {
  if (positions.length === 0) return <Empty text="No active open positions." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className={TH_ROW}>
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
              <tr key={pos.id} className={ROW}>
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
                <td className="text-right font-mono text-text-secondary pr-4 whitespace-nowrap">{fmtTs(pos.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Open Orders (live) — resting limit orders on Delta ────────────────────
function OpenOrdersTab({ orders }: { orders: any[] }) {
  if (orders.length === 0) return <Empty text="No active open orders." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse whitespace-nowrap">
        <thead>
          <tr className={TH_ROW}>
            <th className="py-2">Symbol</th><th className="text-right">Qty (Lot)</th><th className="text-right">Filled</th>
            <th className="text-right">Size</th><th className="text-right">Notional</th><th>Type</th>
            <th className="text-right">Reduce Only</th><th className="text-right">Limit Price</th>
            <th className="text-right">Exec Price</th><th>TP / SL</th><th className="text-right pr-4">Time</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {orders.map((o: any, i: number) => {
            const size = num(o.size) || 0;
            const sell = String(o.side) === 'sell';
            const qty = sell ? -size : size;
            const unfilled = num(o.unfilled_size) ?? size;
            const filled = Math.max(0, size - unfilled);
            const cv = num(o.product?.contract_value) ?? 0.001;
            const unit = o.product?.underlying_asset?.symbol || 'BTC';
            const sizeBtc = parseFloat((size * cv).toFixed(6));
            const idx = num(o.spot_price);
            const notional = idx != null ? Math.abs(size) * cv * idx : null;
            const tp = num(o.bracket_take_profit_price);
            const sl = num(o.bracket_stop_loss_price);
            return (
              <tr key={o.id ?? o.client_order_id ?? i} className={ROW}>
                <td className="py-2.5 font-bold text-text-primary"><Rail sell={sell} />{o.product_symbol || '—'}</td>
                <td className={`text-right font-mono font-bold ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{qty > 0 ? '+' : ''}{qty}</td>
                <td className="text-right font-mono text-text-secondary">{fmtNum(filled, 0)}</td>
                <td className="text-right font-mono text-text-secondary">{sizeBtc} {unit}</td>
                <td className="text-right font-mono text-text-primary">{notional != null ? `$${fmtNum(notional)}` : '—'}</td>
                <td className="text-text-secondary">{cleanType(o.order_type)}</td>
                <td className="text-right text-text-secondary">{o.reduce_only ? '✓' : '—'}</td>
                <td className="text-right font-mono text-text-primary">{fmtNum(o.limit_price)}</td>
                <td className="text-right font-mono text-text-secondary">{o.average_fill_price ? fmtNum(o.average_fill_price) : '—'}</td>
                <td className="text-[11px]">
                  <span className="text-emerald-400">TP {tp != null ? fmtNum(tp) : '—'}</span>
                  <span className="text-text-muted"> · </span>
                  <span className="text-rose-400">SL {sl != null ? fmtNum(sl) : '—'}</span>
                </td>
                <td className="text-right font-mono text-text-secondary pr-4">{fmtTs(o.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Stop Orders (live) — reduce-only stops resting on Delta ────────────────
function StopOrdersTab({ orders }: { orders: any[] }) {
  if (orders.length === 0) return <Empty text="No active stop orders." />;
  const typeLabel = (o: any) => {
    const t = String(o.stop_order_type || '');
    const kind = t.includes('take_profit') ? 'TP' : t.includes('stop_loss') ? 'SL' : 'Stop';
    return o.bracket_order ? `Bracket - ${kind}` : kind;
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse whitespace-nowrap">
        <thead>
          <tr className={TH_ROW}>
            <th className="py-2">Symbol</th><th className="text-right">Qty (Lot)</th><th className="text-right">Size</th>
            <th className="text-right">Notional</th><th className="text-right">Trigger Price</th><th>Trigger Index</th>
            <th className="text-right">Triggering Price</th><th>Type</th><th className="text-right">Limit Price</th>
            <th className="text-right">Reduce Only</th><th>Status</th><th className="text-right pr-4">Time</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {orders.map((o: any, i: number) => {
            const size = num(o.size) || 0;
            const sell = String(o.side) === 'sell';
            const qty = sell ? -size : size;
            const cv = num(o.product?.contract_value) ?? 0.001;
            const unit = o.product?.underlying_asset?.symbol || 'BTC';
            const sizeBtc = parseFloat((size * cv).toFixed(6));
            const idx = num(o.spot_price);
            const notional = idx != null ? Math.abs(size) * cv * idx : null;
            const tm = String(o.stop_trigger_method || '');
            const trigMethod = tm === 'spot_price' ? 'Index Price' : tm === 'mark_price' ? 'Mark Price' : (o.stop_trigger_method || '—');
            const trig = num(o.stop_price);
            const label = typeLabel(o);
            const status = String(o.state) === 'pending' ? 'Untriggered' : String(o.state || '—');
            return (
              <tr key={o.id ?? o.client_order_id ?? i} className={ROW}>
                <td className="py-2.5 font-bold text-text-primary"><Rail sell={sell} />{o.product_symbol || '—'}</td>
                <td className={`text-right font-mono font-bold ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{qty > 0 ? '+' : ''}{qty}</td>
                <td className="text-right font-mono text-text-secondary">{sizeBtc} {unit}</td>
                <td className="text-right font-mono text-text-primary">{notional != null ? `$${fmtNum(notional)}` : '—'}</td>
                <td className="text-right font-mono font-bold text-text-primary">{trig != null ? fmtNum(trig) : '—'}</td>
                <td className="text-text-secondary">{trigMethod}</td>
                <td className="text-right font-mono text-text-secondary">{idx != null ? fmtNum(idx) : '—'}</td>
                <td className={`font-semibold text-[11px] ${label.includes('TP') ? 'text-emerald-400' : 'text-rose-400'}`}>{label}</td>
                <td className="text-right font-mono text-text-secondary">{o.limit_price ? fmtNum(o.limit_price) : '—'}</td>
                <td className="text-right text-text-secondary">{o.reduce_only ? '✓' : '—'}</td>
                <td className="text-text-muted text-[11px]">{status}</td>
                <td className="text-right font-mono text-text-secondary pr-4">{fmtTs(o.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Fills (live) — individual leg executions from Delta ────────────────────
function FillsTab({ fills }: { fills: any[] }) {
  if (fills.length === 0) return <Empty text="No fills yet." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse whitespace-nowrap">
        <thead>
          <tr className={TH_ROW}>
            <th className="py-2">Symbol</th><th className="text-right">Fill Qty (Lot)</th><th>Side</th>
            <th className="text-right">Order Qty (Lot)</th><th className="text-right">Exec Price</th>
            <th className="text-right">Notional</th><th className="text-right">Size</th>
            <th>Fill Type</th><th>Order Type</th><th>Role</th><th className="text-right pr-4">Time</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04] font-medium">
          {fills.map((f: any, i: number) => {
            const size = num(f.size) || 0;
            const sell = String(f.side) === 'sell';
            const cv = num(f.product?.contract_value) ?? 0.001;
            const unit = f.product?.underlying_asset?.symbol || 'BTC';
            const sizeBtc = parseFloat((size * cv).toFixed(6)) * (sell ? -1 : 1);
            const notional = num(f.notional);
            const orderQty = num(f.meta_data?.order_size) ?? size;
            const orderType = f.meta_data?.order_type;
            const fillType = cap(f.fill_type) === '—' ? 'Normal' : cap(f.fill_type);
            return (
              <tr key={f.id ?? `${f.order_id}-${f.created_at}-${i}`} className={ROW}>
                <td className="py-2.5 font-bold text-text-primary"><Rail sell={sell} />{f.product_symbol || '—'}</td>
                <td className={`text-right font-mono font-bold ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{fmtNum(size, 0)}</td>
                <td className={`font-semibold ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{cap(f.side)}</td>
                <td className="text-right font-mono text-text-secondary">{fmtNum(orderQty, 0)}</td>
                <td className="text-right font-mono text-text-primary">{fmtNum(f.price)}</td>
                <td className="text-right font-mono text-text-secondary">{notional != null ? `$${notional.toLocaleString('en-US', { maximumFractionDigits: 4 })}` : '—'}</td>
                <td className={`text-right font-mono ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{sizeBtc > 0 ? '+' : ''}{sizeBtc} {unit}</td>
                <td className="text-text-secondary">{fillType}</td>
                <td className="text-text-secondary">{cleanType(orderType)}</td>
                <td className="text-text-secondary">{cap(f.role)}</td>
                <td className="text-right font-mono text-text-secondary pr-4">{fmtTs(f.created_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Risk & Margin (live) — summary cards + per-leg margin/liquidation ──────
function RiskTab({ positions, wallet }: { positions: any[]; wallet?: number }) {
  const open = (positions || []).filter((p) => Number(p.size) !== 0);
  const pnlOf = (p: any) => {
    const size = num(p.size) || 0;
    const cv = num(p.product?.contract_value) ?? 0.001;
    const entry = num(p.entry_price);
    const mark = num(p.mark_price);
    return (mark != null && entry != null) ? size * cv * (mark - entry) : (num(p.unrealized_pnl) ?? 0);
  };
  const totalMargin = open.reduce((s, p) => s + (num(p.margin) || 0), 0);
  const totalUnrl = open.reduce((s, p) => s + pnlOf(p), 0);
  const w = num(wallet);
  const usedPct = w ? Math.min(100, (totalMargin / w) * 100) : 0;

  const cards = [
    { label: 'Wallet Balance', big: w != null ? `$${fmtNum(w)}` : '—', sub: 'USDT on Delta', pct: 100, color: 'bg-blue-400', val: 'text-text-primary' },
    { label: 'Margin Used', big: `$${fmtNum(totalMargin)}`, sub: w ? `${usedPct.toFixed(1)}% of wallet` : `${open.length} position${open.length !== 1 ? 's' : ''}`, pct: usedPct || 100, color: 'bg-blue-400', val: 'text-text-primary' },
    { label: 'Unrealized P&L', big: `${totalUnrl >= 0 ? '+' : ''}${fmtNum(totalUnrl)}`, sub: 'Mark-to-market', pct: Math.min(100, (Math.abs(totalUnrl) / (totalMargin || 1)) * 100), color: totalUnrl >= 0 ? 'bg-emerald-400' : 'bg-rose-400', val: pnlCls(totalUnrl) },
    { label: 'Open Legs', big: `${open.length}`, sub: 'Positions on exchange', pct: 100, color: 'bg-emerald-400', val: 'text-text-primary' },
  ];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {cards.map((c, i) => (
          <div key={i} className="rounded-xl border border-bg-border bg-bg-panel/40 p-3">
            <h4 className="text-[9px] font-bold uppercase tracking-wider text-text-muted">{c.label}</h4>
            <span className={`block mt-1 font-mono font-bold text-base ${c.val}`}>{c.big}</span>
            <div className="mt-2 h-1 rounded-full bg-bg-border overflow-hidden">
              <div className={`h-full rounded-full ${c.color}`} style={{ width: `${c.pct}%` }} />
            </div>
            <span className="block mt-1.5 text-[10px] text-text-muted">{c.sub}</span>
          </div>
        ))}
      </div>

      {open.length === 0 ? (
        <Empty text="No open exposure." />
      ) : (
        <div className="overflow-x-auto border-t border-bg-border pt-4">
          <table className="w-full text-left text-xs border-collapse whitespace-nowrap">
            <thead>
              <tr className={TH_ROW}>
                <th className="py-2">Instrument</th><th>Size</th><th className="text-right">Entry</th>
                <th className="text-right">Mark</th><th className="text-right">Liq. Price</th>
                <th className="text-right">Margin</th><th className="text-right pr-4">Unrealized P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04] font-medium">
              {open.map((p, i) => {
                const size = num(p.size) || 0;
                const long = size > 0;
                const pnl = pnlOf(p);
                return (
                  <tr key={p.product_id ?? p.product_symbol ?? i} className={ROW}>
                    <td className="py-2.5 font-bold text-text-primary">{p.product_symbol || '—'}</td>
                    <td>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold ring-1 ${long ? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/25' : 'bg-rose-500/10 text-rose-400 ring-rose-500/25'}`}>{long ? 'LONG' : 'SHORT'}</span>
                      <span className="ml-2 font-mono font-semibold text-text-primary">{fmtNum(Math.abs(size), 0)}</span>
                    </td>
                    <td className="text-right font-mono text-text-secondary">{fmtNum(p.entry_price)}</td>
                    <td className="text-right font-mono text-text-secondary">{fmtNum(p.mark_price)}</td>
                    <td className="text-right font-mono text-amber-400">{fmtNum(p.liquidation_price)}</td>
                    <td className="text-right font-mono text-text-primary">{p.margin != null ? `$${fmtNum(p.margin)}` : '—'}</td>
                    <td className={`text-right font-mono pr-4 ${pnlCls(pnl)}`}>{pnl >= 0 ? '+' : ''}{fmtNum(pnl)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Order History (live) — Delta's real order-history feed ─────────────────
function HistoryTab({ history }: { history: any[] }) {
  const [filterDate, setFilterDate] = useState('');
  if (!history?.length) return <Empty text="No recent orders." />;

  const localYmd = (v: any) => {
    try {
      const d = new Date(v);
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    } catch { return ''; }
  };
  const rows = history.filter((o) => !filterDate || localYmd(o.updated_at ?? o.created_at) === filterDate);

  const statusLabel = (o: any) => {
    const s = String(o.state || '').toLowerCase();
    if (s === 'cancelled' || s === 'canceled') return 'Cancelled';
    const filled = (num(o.size) || 0) - (num(o.unfilled_size) ?? 0);
    if (s === 'closed') return filled > 0 ? 'Filled' : 'Cancelled';
    return cap(s);
  };
  const statusCls = (l: string) => (l === 'Filled' ? 'text-emerald-400' : l === 'Cancelled' ? 'text-rose-400' : 'text-text-muted');
  const typeLabel = (o: any) => {
    const st = String(o.stop_order_type || '');
    if (o.bracket_order || st) {
      const kind = st.includes('take_profit') ? 'TP' : st.includes('stop_loss') ? 'SL' : null;
      if (kind) return `Bracket - ${kind}`;
    }
    return cleanType(o.order_type);
  };
  const exitReasonOf = (o: any) => {
    const st = String(o.stop_order_type || '');
    if (st.includes('take_profit')) return 'Take Profit';
    if (st.includes('stop_loss')) return 'Stop Loss';
    const coid = String(o.client_order_id || '');
    if (/-CAX[BS]\b/.test(coid)) return 'Close All';
    if (/-MX[BS]\b/.test(coid)) return 'Manual Exit';
    if (/-CX\b/.test(coid)) return 'Manual Close';
    if (/-TP(\b|-)/.test(coid)) return 'Take Profit';
    return '—';
  };
  const reasonCls = (r: string) => (r === 'Take Profit' ? 'text-emerald-400' : r === 'Stop Loss' ? 'text-rose-400' : r === '—' ? 'text-text-muted' : 'text-text-secondary');

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end gap-2">
        <input
          type="date"
          value={filterDate}
          onChange={(e) => setFilterDate(e.target.value)}
          className="bg-bg-panel border border-bg-border text-text-secondary rounded-md px-2 py-1 text-[11px] font-semibold outline-none [color-scheme:dark]"
        />
        {filterDate && (
          <button onClick={() => setFilterDate('')} className="px-2 py-1 text-[10px] font-bold text-text-muted border border-bg-border rounded-md hover:text-text-secondary">All</button>
        )}
      </div>
      {rows.length === 0 ? <Empty text="No orders match the selected date." /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse whitespace-nowrap">
            <thead>
              <tr className={TH_ROW}>
                <th className="py-2">Symbol</th><th>Side</th><th>Status</th><th className="text-right">Qty (Lot)</th>
                <th className="text-right">Filled</th><th>Type</th><th className="text-right">Limit Price</th>
                <th className="text-right">Trigger Price</th><th className="text-right">Exec Price</th><th className="text-right">Size</th>
                <th className="text-right">Realized PnL</th><th>Exit Reason</th>
                <th className="text-right">Reduce Only</th><th className="text-right">Order ID</th><th className="text-right pr-4">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04] font-medium">
              {rows.map((o: any, i: number) => {
                const size = num(o.size) || 0;
                const sell = String(o.side) === 'sell';
                const filled = Math.max(0, size - (num(o.unfilled_size) ?? 0));
                const cv = num(o.product?.contract_value) ?? 0.001;
                const unit = o.product?.underlying_asset?.symbol || 'BTC';
                const sizeBtc = parseFloat((size * cv).toFixed(6)) * (sell ? -1 : 1);
                const rpnl = num(o.meta_data?.pnl ?? o.realized_pnl);
                const status = statusLabel(o);
                const sideLabel = `${o.reduce_only ? 'Close' : 'Open'} ${cap(o.side)}`;
                const isLimitType = String(o.order_type || '').includes('limit');
                const trigger = num(o.stop_price) ?? num(o.bracket_stop_loss_price) ?? num(o.bracket_take_profit_price);
                const reason = exitReasonOf(o);
                return (
                  <tr key={o.id ?? `${o.client_order_id}-${i}`} className={ROW}>
                    <td className="py-2.5 font-bold text-text-primary"><Rail sell={sell} />{o.product_symbol || '—'}</td>
                    <td className={`font-semibold ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{sideLabel}</td>
                    <td className={`font-semibold ${statusCls(status)}`}>{status}</td>
                    <td className="text-right font-mono font-bold text-text-primary">{Math.abs(size)}</td>
                    <td className="text-right font-mono text-text-secondary">{fmtNum(filled, 0)}</td>
                    <td className="text-text-secondary">{typeLabel(o)}</td>
                    <td className="text-right font-mono text-text-secondary">{isLimitType && o.limit_price ? fmtNum(o.limit_price) : '—'}</td>
                    <td className="text-right font-mono text-text-secondary">{trigger != null ? fmtNum(trigger) : '—'}</td>
                    <td className="text-right font-mono text-text-primary">{o.average_fill_price ? fmtNum(o.average_fill_price) : '—'}</td>
                    <td className={`text-right font-mono ${sell ? 'text-rose-400' : 'text-emerald-400'}`}>{sizeBtc > 0 ? '+' : ''}{sizeBtc} {unit}</td>
                    <td className={`text-right font-mono ${rpnl == null ? 'text-text-muted' : pnlCls(rpnl)}`}>{rpnl != null ? `${rpnl >= 0 ? '+' : ''}${fmtNum(rpnl)}` : '—'}</td>
                    <td className={`text-[11px] ${reasonCls(reason)}`}>{reason}</td>
                    <td className="text-right text-text-secondary">{o.reduce_only ? '✓' : '✕'}</td>
                    <td className="text-right font-mono text-text-muted text-[11px]">{o.id ?? '—'}</td>
                    <td className="text-right font-mono text-text-muted text-[11px] pr-4">{fmtTs(o.updated_at ?? o.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
