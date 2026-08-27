'use client';
import React, { useMemo, useState } from 'react';
import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, Clock, Download,
  FileText, Send, XCircle, RefreshCw,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, saveBlob } from '@/lib/api';
import { useComparison } from '@/hooks/useComparison';
import { Loader } from '@/components/shared/Loader';

/**
 * Order-by-order comparison: every master order against what each follower
 * punched for it.
 *
 * This deliberately does NOT grade net position. The 15s reconciler repairs
 * position, so a position view can only ever say "matched" — on 2026-08-27 it
 * reported a clean day while the engine was punching double-sized orders and
 * unwinding them a minute later. The order is what the engine actually did, at
 * the moment it did it, before anything tidied up after it.
 */

const VERDICT: Record<string, { label: string; cls: string; help: string }> = {
  matched: {
    label: 'MATCHED', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    help: 'Follower punched the right size for this master order.',
  },
  oversized: {
    label: 'OVER-PUNCHED', cls: 'bg-red-500/10 text-red-400 border-red-500/20',
    help: 'Follower ordered MORE than its proportional target. The reconciler may unwind it later, but the round trip costs fees.',
  },
  undersized: {
    label: 'UNDER-PUNCHED', cls: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    help: 'Follower ordered LESS than its proportional target.',
  },
  missing: {
    label: 'MISSING', cls: 'bg-red-500/10 text-red-400 border-red-500/20',
    help: 'No follower order at all for this master order.',
  },
  cancel_missed: {
    label: 'CANCEL MISSED', cls: 'bg-red-500/10 text-red-400 border-red-500/20',
    help: 'The master cancelled its order; the follower’s mirror is still resting on the exchange.',
  },
  extra: {
    label: 'UNWANTED FILL', cls: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    help: 'The master cancelled without filling, but the follower traded anyway.',
  },
  cancelled_ok: {
    label: 'CANCELLED OK', cls: 'bg-emerald-500/10 text-emerald-400/90 border-emerald-500/20',
    help: 'The master cancelled and the follower cancelled too — correctly mirrored.',
  },
  ladder: {
    label: 'LADDER', cls: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
    help: 'One rung of a laddered action. The engine mirrors a ladder as ONE order, so the ladder’s total is judged rather than this rung.',
  },
  skipped: {
    label: 'SKIPPED', cls: 'bg-slate-500/10 text-slate-300 border-slate-500/20',
    help: 'The engine deliberately did not copy this (e.g. a risk check).',
  },
  unsized: {
    label: 'NO TARGET', cls: 'bg-violet-500/10 text-violet-300 border-violet-500/20',
    help: 'This follower’s allocation settings give no target to compare against.',
  },
  unreadable: {
    label: 'UNREADABLE', cls: 'bg-red-500/10 text-red-400 border-red-500/20',
    help: 'This account’s orders could not be read — nothing was verified.',
  },
};

const MISMATCH = new Set(['oversized', 'undersized', 'missing', 'cancel_missed', 'extra']);

function VerdictBadge({ v }: { v: string }) {
  const m = VERDICT[v] || { label: (v || '?').toUpperCase(), cls: 'bg-slate-500/10 text-slate-300 border-slate-500/20', help: '' };
  return (
    <span title={m.help}
      className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[9px] font-bold whitespace-nowrap ${m.cls}`}>
      {m.label}
    </span>
  );
}

function ms(v: number | null | undefined) {
  if (v === null || v === undefined) return '—';
  const a = Math.abs(v), sign = v < 0 ? '-' : '';
  if (a < 1000) return `${sign}${a.toFixed(0)}ms`;
  if (a < 60_000) return `${sign}${(a / 1000).toFixed(2)}s`;
  return `${sign}${(a / 60_000).toFixed(1)}m`;
}
function lots(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : `${v}`;
}
function ratio(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : v.toFixed(5);
}
function clock(iso: string | null | undefined) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
}
function todayIST() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}

function Stat({ label, value, sub, tone }: {
  label: string; value: React.ReactNode; sub?: string;
  tone?: 'good' | 'bad' | 'warn' | 'neutral';
}) {
  const color = tone === 'good' ? 'text-emerald-400'
    : tone === 'bad' ? 'text-rose-400'
    : tone === 'warn' ? 'text-amber-400' : 'text-text-primary';
  return (
    <div className="card-premium p-4">
      <span className="text-[10px] font-semibold text-text-muted uppercase tracking-[0.12em]">{label}</span>
      <div className={`mt-2 text-[22px] leading-none font-bold tracking-tight font-mono ${color}`}>{value}</div>
      {sub && <p className="text-[11px] text-text-muted mt-2 leading-snug">{sub}</p>}
    </div>
  );
}

export function ComparisonView({ ownerId, ownerLabel }: { ownerId?: string; ownerLabel?: string }) {
  const [day, setDay] = useState(todayIST());
  const [filter, setFilter] = useState<'all' | 'mismatch' | 'matched'>('all');
  const [busy, setBusy] = useState<string | null>(null);

  const isToday = day === todayIST();
  const params = useMemo(() => ({ date: day, ...(ownerId ? { owner_id: ownerId } : {}) }), [day, ownerId]);
  const { data, isLoading, isFetching, error, refetch } = useComparison(params, isToday);

  const rows: any[] = data?.rows || [];
  const s = data?.summary;

  /** One flat line per (master order, follower) — the table the desk asked for. */
  const lines = useMemo(() => {
    const out: any[] = [];
    for (const r of rows) {
      for (const l of r.legs || []) out.push({ r, l });
    }
    if (filter === 'mismatch') return out.filter(x => MISMATCH.has(x.l.verdict));
    if (filter === 'matched') return out.filter(x => x.l.verdict === 'matched');
    return out;
  }, [rows, filter]);

  const counts = useMemo(() => {
    let mis = 0, ok = 0, total = 0;
    for (const r of rows) for (const l of r.legs || []) {
      total++;
      if (MISMATCH.has(l.verdict)) mis++;
      else if (l.verdict === 'matched') ok++;
    }
    return { mis, ok, total };
  }, [rows]);

  const download = async (kind: 'csv' | 'html') => {
    setBusy(kind);
    try {
      const blob = kind === 'csv' ? await api.comparison.csv(params) : await api.comparison.html(params);
      saveBlob(blob, `order-match-${day}.${kind}`);
      toast.success(`Report downloaded (${kind.toUpperCase()})`);
    } catch (e: any) { toast.error(e.message || 'Download failed'); } finally { setBusy(null); }
  };

  const sendNow = async () => {
    setBusy('send');
    try {
      const res = await api.comparison.send(day);
      if (res.sent) toast.success('Report sent to Telegram');
      else toast.error(res.reason || 'Report was not sent');
    } catch (e: any) { toast.error(e.message || 'Send failed'); } finally { setBusy(null); }
  };

  const clean = s && s.errors === 0 && !(data?.warnings || []).length;

  return (
    <div className="space-y-5">
      {/* controls */}
      <div className="card-premium p-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-[11px] font-semibold text-text-muted uppercase tracking-[0.1em]">IST day</label>
          <input type="date" value={day} max={todayIST()}
            onChange={e => setDay(e.target.value || todayIST())}
            className="bg-bg-panel border border-bg-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-blue-500/50" />
          {isToday && (
            <span className="text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">LIVE</span>
          )}
        </div>
        {ownerLabel && (
          <span className="text-[11px] text-text-muted">
            Tenant <span className="text-text-secondary font-semibold">{ownerLabel}</span>
          </span>
        )}
        <div className="flex-1" />
        <button onClick={() => refetch()} disabled={isFetching}
          className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-bg-border text-text-secondary hover:text-text-primary hover:bg-bg-panel/60 transition-colors disabled:opacity-50">
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} /> Refresh
        </button>
        <button onClick={() => download('csv')} disabled={!!busy || !data}
          className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-bg-border text-text-secondary hover:text-text-primary hover:bg-bg-panel/60 transition-colors disabled:opacity-50">
          <Download className="w-3.5 h-3.5" /> CSV
        </button>
        <button onClick={() => download('html')} disabled={!!busy || !data}
          className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-bg-border text-text-secondary hover:text-text-primary hover:bg-bg-panel/60 transition-colors disabled:opacity-50">
          <FileText className="w-3.5 h-3.5" /> Doc
        </button>
        <button onClick={sendNow} disabled={!!busy || !data}
          title="Post this day's summary to the Telegram chat now"
          className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-300 hover:bg-blue-500/25 transition-colors disabled:opacity-50">
          <Send className="w-3.5 h-3.5" /> Send to Telegram
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3">
          {(error as any).message || 'Failed to build the comparison.'}
        </div>
      )}
      {(data?.warnings || []).map((w: string, i: number) => (
        <div key={i} className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/30 text-amber-300 text-xs rounded-lg px-4 py-3">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" /> <span>{w}</span>
        </div>
      ))}

      {isLoading && !data && <Loader label="Reading order history from both accounts…" />}

      {data && s && (
        <>
          <div className={`flex items-start gap-3 rounded-xl px-4 py-3.5 border ${
            clean ? 'bg-emerald-500/[0.07] border-emerald-500/25'
              : s.errors > 0 ? 'bg-red-500/[0.07] border-red-500/25'
              : 'bg-amber-500/[0.07] border-amber-500/25'}`}>
            {clean ? <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
              : <XCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />}
            <div className="min-w-0">
              <p className="text-sm font-bold text-text-primary">
                {clean ? 'Every order was punched correctly.'
                  : s.errors > 0 ? `${s.errors} order${s.errors === 1 ? '' : 's'} punched wrong.`
                  : 'No mismatches, but some orders could not be graded.'}
              </p>
              <p className="text-[11px] text-text-muted mt-1 leading-relaxed">
                {s.master_orders} master order{s.master_orders === 1 ? '' : 's'} compared
                against {(data.followers || []).length} active follower
                {(data.followers || []).length === 1 ? '' : 's'} · read from order history,
                so a wrong order shows here even when the reconciler later corrects the position
                {data.extra_follower_orders?.length > 0 && (
                  <> · <span className="text-amber-400 font-semibold">
                    {data.extra_follower_orders.length} order(s) on symbols the master never traded
                  </span></>
                )}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            <Stat label="Master orders" value={s.master_orders} sub={`${lots(data.master?.lots)} lots`} />
            <Stat label="Match rate" value={`${s.match_rate_pct}%`}
              tone={s.match_rate_pct >= 100 ? 'good' : s.match_rate_pct >= 95 ? 'warn' : 'bad'}
              sub={`${s.matched} of ${s.legs} order legs correct`} />
            <Stat label="Unmatched" value={s.errors} tone={s.errors === 0 ? 'good' : 'bad'}
              sub={['oversized', 'undersized', 'missing', 'cancel_missed']
                .filter(k => s.by_verdict?.[k])
                .map(k => `${VERDICT[k].label.toLowerCase()} ${s.by_verdict[k]}`).join(' · ') || 'none'} />
            <Stat label="Median time diff" value={ms(s.median_time_diff_ms)}
              sub={`${s.time_diff_samples} sample${s.time_diff_samples === 1 ? '' : 's'}`} />
            <Stat label="p95 time diff" value={ms(s.p95_time_diff_ms)} sub={`avg ${ms(s.avg_time_diff_ms)}`} />
            <Stat label="Max time diff" value={ms(s.max_time_diff_ms)}
              tone={s.max_time_diff_ms !== null && s.max_time_diff_ms > 30_000 ? 'warn' : 'neutral'}
              sub="follower order − master order" />
          </div>

          {(data.excluded_followers || []).length > 0 && (
            <div className="flex items-start gap-2 bg-slate-500/[0.08] border border-slate-500/25 text-text-secondary text-xs rounded-lg px-4 py-3">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-slate-400" />
              <span>
                <strong>Not graded:</strong>{' '}
                {data.excluded_followers.map((e: any) => `${e.name} (${e.status})`).join(', ')}
                {' — '}the engine doesn&apos;t copy to these, so their orders are their own book.
              </span>
            </div>
          )}

          {/* per follower */}
          <div className="card-premium p-5">
            <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-3">Per follower</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                    <th className="py-2.5 px-3 whitespace-nowrap">Follower</th>
                    <th className="px-3 text-right whitespace-nowrap">Ratio</th>
                    <th className="px-3 text-right whitespace-nowrap">Order legs</th>
                    <th className="px-3 text-right whitespace-nowrap">Matched</th>
                    <th className="px-3 text-right whitespace-nowrap">Unmatched</th>
                    <th className="px-3 text-right whitespace-nowrap">Median</th>
                    <th className="px-3 text-right whitespace-nowrap">Avg</th>
                    <th className="px-3 text-right whitespace-nowrap">Max</th>
                    <th className="px-3">Breakdown</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04] font-medium">
                  {(s.per_follower || []).length === 0 && (
                    <tr><td colSpan={9} className="py-8 text-center text-text-muted">No active followers.</td></tr>
                  )}
                  {(s.per_follower || []).map((f: any) => (
                    <tr key={f.account_id} className="hover:bg-bg-panel/40">
                      <td className="py-2.5 px-3 font-semibold text-text-primary whitespace-nowrap">
                        {f.account_name}
                        {f.unreadable && <span className="ml-2"><VerdictBadge v="unreadable" /></span>}
                      </td>
                      <td className="px-3 text-right font-mono text-text-secondary" title={f.ratio_basis || ''}>{ratio(f.ratio)}</td>
                      <td className="px-3 text-right font-mono text-text-secondary">{f.orders}</td>
                      <td className={`px-3 text-right font-mono font-bold ${
                        f.match_rate_pct === null ? 'text-text-muted'
                          : f.match_rate_pct >= 100 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {f.match_rate_pct === null ? '—' : `${f.match_rate_pct}%`}
                      </td>
                      <td className={`px-3 text-right font-mono font-bold ${f.errors ? 'text-rose-400' : 'text-emerald-400'}`}>{f.errors}</td>
                      <td className="px-3 text-right font-mono text-text-secondary whitespace-nowrap">{ms(f.median_time_diff_ms)}</td>
                      <td className="px-3 text-right font-mono text-text-secondary whitespace-nowrap">{ms(f.avg_time_diff_ms)}</td>
                      <td className="px-3 text-right font-mono text-text-secondary whitespace-nowrap">{ms(f.max_time_diff_ms)}</td>
                      <td className="px-3">
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(f.by_verdict || {}).map(([v, n]) => (
                            <span key={v} className="inline-flex items-center gap-1">
                              <VerdictBadge v={v} />
                              <span className="text-text-muted font-mono text-[10px]">{n as number}</span>
                            </span>
                          ))}
                          {!Object.keys(f.by_verdict || {}).length && <span className="text-text-muted">—</span>}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* THE table */}
          <div className="card-premium p-5">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em]">Order comparison</h3>
              <div className="flex items-center gap-1 bg-bg-panel border border-bg-border rounded-lg p-0.5">
                {([['all', `All (${counts.total})`],
                   ['mismatch', `Unmatched (${counts.mis})`],
                   ['matched', `Matched (${counts.ok})`]] as const).map(([k, label]) => (
                  <button key={k} onClick={() => setFilter(k)}
                    className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors ${
                      filter === k ? 'bg-blue-500/20 text-blue-300' : 'text-text-muted hover:text-text-secondary'}`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <p className="text-[11px] text-text-muted mb-3 leading-relaxed">
              One row per master order per follower. A follower&apos;s order should be
              <strong className="text-text-secondary"> ceil(master lots × ratio)</strong> — anything
              else is over- or under-punched, <em>however tidy the resulting position looks</em>.
              Cancels are compared too.
            </p>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                    <th className="py-2.5 px-3 whitespace-nowrap">Time (IST)</th>
                    <th className="px-3 whitespace-nowrap">Symbol</th>
                    <th className="px-3 whitespace-nowrap">Side</th>
                    <th className="px-3 text-right whitespace-nowrap">Master</th>
                    <th className="px-3 whitespace-nowrap">Follower</th>
                    <th className="px-3 text-center whitespace-nowrap">Verdict</th>
                    <th className="px-3 text-right whitespace-nowrap">Ratio</th>
                    <th className="px-3 text-right whitespace-nowrap"
                      title="What the follower should have punched / what it actually punched">
                      Target / Punched
                    </th>
                    <th className="px-3 text-right whitespace-nowrap">Time diff</th>
                    <th className="px-3 min-w-[220px]">Reason</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04] font-medium">
                  {lines.length === 0 && (
                    <tr><td colSpan={10} className="py-12 text-center text-text-muted">
                      {counts.total === 0 ? 'No master orders on this day.' : 'Nothing matches this filter.'}
                    </td></tr>
                  )}
                  {lines.map(({ r, l }, i) => {
                    const buy = r.side === 'buy';
                    const bad = MISMATCH.has(l.verdict);
                    return (
                      <tr key={`${r.master_order_id}-${l.account_id}-${i}`}
                        className={bad
                          ? 'bg-red-500/[0.09] hover:bg-red-500/[0.14] border-l-2 border-l-rose-500'
                          : 'hover:bg-bg-panel/40'}>
                        <td className="py-2.5 px-3 font-mono text-text-secondary whitespace-nowrap">{clock(r.placed_at)}</td>
                        <td className="px-3 font-mono font-semibold text-text-primary whitespace-nowrap">{r.symbol}</td>
                        <td className="px-3">
                          <span className={`inline-flex items-center gap-1 font-bold ${buy ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {buy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                            {(r.side || '').toUpperCase()}
                          </span>
                        </td>
                        {/* master lots with its order state underneath */}
                        <td className="px-3 text-right font-mono text-text-primary leading-tight whitespace-nowrap">
                          {lots(r.master_lots)}
                          <span className="block text-[10px] text-text-muted font-sans">{r.master_state}</span>
                        </td>
                        <td className="px-3 text-text-primary whitespace-nowrap">{l.account_name}</td>
                        <td className="px-3 text-center"><VerdictBadge v={l.verdict} /></td>
                        <td className="px-3 text-right font-mono text-text-secondary whitespace-nowrap"
                          title={`target ratio ${ratio(l.ratio_target)}`}>
                          {ratio(l.ratio_actual)}
                        </td>
                        {/* target / punched — the comparison, side by side */}
                        <td className="px-3 text-right font-mono whitespace-nowrap">
                          <span className="text-text-muted" title={l.target_basis || ''}>{lots(l.target_lots)}</span>
                          <span className="text-text-muted mx-1.5">/</span>
                          <span className={bad ? 'text-rose-400 font-bold' : 'text-text-primary font-semibold'}>
                            {lots(l.placed_lots)}
                          </span>
                        </td>
                        <td className={`px-3 text-right font-mono whitespace-nowrap ${
                          l.time_diff_ms !== null && Math.abs(l.time_diff_ms) > 30_000 ? 'text-amber-400' : 'text-text-secondary'}`}>
                          {ms(l.time_diff_ms)}
                        </td>
                        {/* reason only where something is actually wrong */}
                        <td className="px-3 text-rose-300/90 leading-snug">{bad ? (l.note || l.leg_reason || '') : ''}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {(data.extra_follower_orders || []).length > 0 && (
            <div className="card-premium p-5">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-1">
                Orders on symbols the master never traded ({data.extra_follower_orders.length})
              </h3>
              <p className="text-[11px] text-text-muted mb-3 leading-relaxed">
                Follower orders with no master counterpart anywhere in this window — usually a
                follower trading its own book.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px]">
                      <th className="py-2.5 px-3">Time (IST)</th><th className="px-3">Follower</th>
                      <th className="px-3">Symbol</th><th className="px-3">Side</th>
                      <th className="px-3 text-right">Lots</th><th className="px-3 text-right">Filled</th>
                      <th className="px-3">State</th><th className="px-3">Order id</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04] font-medium">
                    {data.extra_follower_orders.map((e: any) => (
                      <tr key={`${e.account_id}-${e.follower_order_id}`} className="hover:bg-bg-panel/40">
                        <td className="py-2.5 px-3 font-mono text-text-secondary whitespace-nowrap">{clock(e.placed_at)}</td>
                        <td className="px-3 font-semibold text-text-primary whitespace-nowrap">{e.account_name}</td>
                        <td className="px-3 font-mono text-text-primary whitespace-nowrap">{e.symbol}</td>
                        <td className={`px-3 font-bold ${e.side === 'buy' ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {(e.side || '').toUpperCase()}
                        </td>
                        <td className="px-3 text-right font-mono text-text-primary">{lots(e.lots)}</td>
                        <td className="px-3 text-right font-mono text-text-secondary">{lots(e.filled)}</td>
                        <td className="px-3 text-text-muted">{e.state}</td>
                        <td className="px-3 font-mono text-text-muted">{e.follower_order_id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="card-premium p-5">
            <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-3">How to read this</h3>
            <div className="grid sm:grid-cols-2 gap-x-8 gap-y-2">
              {Object.entries(VERDICT).map(([k, v]) => (
                <div key={k} className="flex items-start gap-2">
                  <span className="shrink-0 mt-0.5"><VerdictBadge v={k} /></span>
                  <span className="text-[11px] text-text-muted leading-relaxed">{v.help}</span>
                </div>
              ))}
            </div>
            <p className="text-[11px] text-text-muted mt-4 leading-relaxed border-t border-bg-border pt-3">
              <Clock className="w-3 h-3 inline mr-1 -mt-0.5" />
              <strong className="text-text-secondary">Time diff</strong> is when the follower&apos;s
              order was placed minus the master&apos;s.{' '}
              <strong className="text-text-secondary">Why orders and not positions:</strong> the 15s
              reconciler repairs net position, so a position view reports &quot;matched&quot; even on a
              day the engine punched double-sized orders and unwound them a minute later. The order
              is what the engine actually did, before anything tidied up after it. Jittered SL/TP
              orders are excluded — they are not one-for-one mirrors by design — and a rung of a
              laddered action is judged on the ladder&apos;s total, since the engine mirrors a ladder
              as one order.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
