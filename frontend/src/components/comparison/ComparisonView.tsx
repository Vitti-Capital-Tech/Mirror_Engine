'use client';
import React, { useMemo, useState } from 'react';
import {
  AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, ChevronDown,
  Clock, Download, FileText, Send, XCircle, RefreshCw, Link2, HelpCircle,
} from 'lucide-react';
import toast from 'react-hot-toast';
import { api, saveBlob } from '@/lib/api';
import { useComparison } from '@/hooks/useComparison';
import { Loader } from '@/components/shared/Loader';

/**
 * The comparison tab: every master fill of a day, against what each follower
 * did about it.
 *
 * The design rule throughout is that a verdict must never overstate what is
 * known. "Missing" means the exchange says the follower has no fill and the
 * engine recorded no reason — it is not used for an account that couldn't be
 * read, an order still resting, or a copy the engine deliberately skipped.
 * Those get their own labels, and only genuine mismatches feed the error count
 * the desk reads each morning.
 */

// ---------------------------------------------------------------- verdicts
type Verdict = 'matched' | 'short' | 'over' | 'missing' | 'resting'
  | 'skipped' | 'unsized' | 'unreadable';

const VERDICT: Record<string, { label: string; cls: string; help: string }> = {
  matched: {
    label: 'MATCHED', cls: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    help: 'Follower filled at least its proportional target.',
  },
  ladder: {
    label: 'LADDER', cls: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
    help: 'One rung of a laddered exit. The master splits the exit across many orders and the engine mirrors it as one — so the verdict is the group total, not this rung.',
  },
  short: {
    label: 'SHORT', cls: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    help: 'Follower filled less than its proportional target.',
  },
  over: {
    label: 'OVER', cls: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
    help: 'Follower filled more than its proportional target.',
  },
  missing: {
    label: 'MISSING', cls: 'bg-red-500/10 text-red-400 border-red-500/20',
    help: 'No follower fill and no copy record explaining why.',
  },
  resting: {
    label: 'RESTING', cls: 'bg-sky-500/10 text-sky-400 border-sky-500/20',
    help: 'Mirrored order is on the exchange, not filled yet.',
  },
  skipped: {
    label: 'SKIPPED', cls: 'bg-slate-500/10 text-slate-300 border-slate-500/20',
    help: 'The engine deliberately did not copy this (e.g. a risk check).',
  },
  unsized: {
    label: 'NO TARGET', cls: 'bg-violet-500/10 text-violet-300 border-violet-500/20',
    help: 'Follower filled, but its allocation settings give no target to compare against.',
  },
  unreadable: {
    label: 'UNREADABLE', cls: 'bg-red-500/10 text-red-400 border-red-500/20',
    help: 'This account’s fills could not be read — nothing was verified.',
  },
};

const MISMATCH = new Set(['missing', 'short', 'over']);

function VerdictBadge({ v }: { v: string }) {
  const m = VERDICT[v] || { label: (v || '?').toUpperCase(), cls: 'bg-slate-500/10 text-slate-300 border-slate-500/20', help: '' };
  return (
    <span title={m.help}
      className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[9px] font-bold whitespace-nowrap ${m.cls}`}>
      {m.label}
    </span>
  );
}

// ---------------------------------------------------------------- formatting
function ms(v: number | null | undefined) {
  if (v === null || v === undefined) return '—';
  const a = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (a < 1000) return `${sign}${a.toFixed(0)}ms`;
  if (a < 60_000) return `${sign}${(a / 1000).toFixed(2)}s`;
  return `${sign}${(a / 60_000).toFixed(1)}m`;
}
function lots(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : `${v}`;
}
/** Signed lots — the sign is the position direction, so never drop it. */
function signed(v: number | null | undefined) {
  if (v === null || v === undefined) return '—';
  return v > 0 ? `+${v}` : `${v}`;
}
function px(v: number | null | undefined) {
  return v === null || v === undefined ? '—' : `${v}`;
}
/** IST clock — the timezone the desk and the report both work in. */
function clock(iso: string | null | undefined) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Kolkata', hour12: false });
}
function todayIST() {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit',
  }).format(new Date());
}

// ---------------------------------------------------------------- stat card
function Stat({ label, value, sub, tone }: {
  label: string; value: React.ReactNode; sub?: string;
  tone?: 'good' | 'bad' | 'warn' | 'neutral';
}) {
  const color =
    tone === 'good' ? 'text-emerald-400'
    : tone === 'bad' ? 'text-rose-400'
    : tone === 'warn' ? 'text-amber-400'
    : 'text-text-primary';
  return (
    <div className="card-premium p-4">
      <span className="text-[10px] font-semibold text-text-muted uppercase tracking-[0.12em]">{label}</span>
      <div className={`mt-2 text-[22px] leading-none font-bold tracking-tight font-mono ${color}`}>{value}</div>
      {sub && <p className="text-[11px] text-text-muted mt-2 leading-snug">{sub}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- main view
export function ComparisonView({ ownerId, ownerLabel }: { ownerId?: string; ownerLabel?: string }) {
  const [day, setDay] = useState(todayIST());
  const [filter, setFilter] = useState<'all' | 'mismatch' | 'matched'>('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);

  const isToday = day === todayIST();
  const params = useMemo(
    () => ({ date: day, ...(ownerId ? { owner_id: ownerId } : {}) }),
    [day, ownerId],
  );
  const { data, isLoading, isFetching, error, refetch } = useComparison(params, isToday);

  const rows: any[] = data?.rows || [];
  const s = data?.summary;

  const shown = useMemo(() => {
    if (filter === 'mismatch') return rows.filter(r => MISMATCH.has(r.status));
    if (filter === 'matched') return rows.filter(r => r.status === 'matched');
    return rows;
  }, [rows, filter]);

  const toggle = (id: string) => setExpanded(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const download = async (kind: 'csv' | 'html') => {
    setBusy(kind);
    try {
      const blob = kind === 'csv'
        ? await api.comparison.csv(params)
        : await api.comparison.html(params);
      saveBlob(blob, `fill-match-${day}.${kind}`);
      toast.success(`Report downloaded (${kind.toUpperCase()})`);
    } catch (e: any) {
      toast.error(e.message || 'Download failed');
    } finally {
      setBusy(null);
    }
  };

  const sendNow = async () => {
    setBusy('send');
    try {
      const res = await api.comparison.send(day);
      if (res.sent) toast.success('Report sent to Telegram');
      else toast.error(res.reason || 'Report was not sent');
    } catch (e: any) {
      toast.error(e.message || 'Send failed');
    } finally {
      setBusy(null);
    }
  };

  const clean = s && s.errors === 0 && !(data?.warnings || []).length;

  return (
    <div className="space-y-5">
      {/* ------------------------------------------------- controls */}
      <div className="card-premium p-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <label className="text-[11px] font-semibold text-text-muted uppercase tracking-[0.1em]">
            IST day
          </label>
          <input
            type="date"
            value={day}
            max={todayIST()}
            onChange={e => setDay(e.target.value || todayIST())}
            className="bg-bg-panel border border-bg-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-blue-500/50"
          />
          {isToday && (
            <span className="text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
              LIVE
            </span>
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

      {isLoading && !data && <Loader label="Reading fills from both accounts…" />}

      {data && s && (
        <>
          {/* --------------------------------------------- verdict banner */}
          <div className={`flex items-start gap-3 rounded-xl px-4 py-3.5 border ${
            clean
              ? 'bg-emerald-500/[0.07] border-emerald-500/25'
              : s.errors > 0
                ? 'bg-red-500/[0.07] border-red-500/25'
                : 'bg-amber-500/[0.07] border-amber-500/25'
          }`}>
            {clean
              ? <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0 mt-0.5" />
              : <XCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />}
            <div className="min-w-0">
              <p className="text-sm font-bold text-text-primary">
                {clean
                  ? 'Accounts match for this day.'
                  : s.errors > 0
                    ? `${s.errors} leg${s.errors === 1 ? '' : 's'} need attention.`
                    : 'No errors, but some legs could not be graded.'}
              </p>
              <p className="text-[11px] text-text-muted mt-1 leading-relaxed">
                {s.master_orders} master order{s.master_orders === 1 ? '' : 's'} compared
                against {(data.followers || []).length} active follower
                {(data.followers || []).length === 1 ? '' : 's'} · fills read straight from
                the exchange, then reconciled per symbol and side
                {s.unmatched_follower_fills > 0 && (
                  <> · <span className="text-amber-400 font-semibold">
                    {s.unmatched_follower_fills} follower fill
                    {s.unmatched_follower_fills === 1 ? '' : 's'} unexplained
                  </span></>
                )}
              </p>
            </div>
          </div>

          {/* --------------------------------------------- headline stats */}
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
            <Stat label="Master orders" value={s.master_orders}
              sub={`${lots(data.master?.lots)} lots filled`} />
            <Stat label="Match rate" value={`${s.match_rate_pct}%`}
              tone={s.match_rate_pct >= 100 ? 'good' : s.match_rate_pct >= 95 ? 'warn' : 'bad'}
              sub={`${s.groups_matched} of ${s.groups} symbols reconcile on net position`} />
            <Stat label="Errors" value={s.errors}
              tone={s.errors === 0 ? 'good' : 'bad'}
              sub={`missing ${s.groups_by_verdict?.missing || 0} · short ${s.groups_by_verdict?.short || 0} · over ${s.groups_by_verdict?.over || 0}`} />
            <Stat label="Excess churn" value={lots(s.excess_churn_lots ?? 0)}
              tone={s.churn_symbols ? 'warn' : 'good'}
              sub={s.churn_symbols
                ? `${s.churn_symbols} symbol${s.churn_symbols === 1 ? '' : 's'} round-tripped`
                : 'no wasted turnover'} />
            <Stat label="Median delay" value={ms(s.median_delay_ms)}
              sub={`${s.delay_samples} sample${s.delay_samples === 1 ? '' : 's'}`} />
            <Stat label="Avg delay" value={ms(s.avg_delay_ms)}
              sub={`p95 ${ms(s.p95_delay_ms)}`} />
            <Stat label="Max delay" value={ms(s.max_delay_ms)}
              tone={s.max_delay_ms !== null && s.max_delay_ms > 30_000 ? 'warn' : 'neutral'}
              sub="follower fill − master fill" />
          </div>

          {/* ------------------------------------- not graded (not copied to) */}
          {(data.excluded_followers || []).length > 0 && (
            <div className="flex items-start gap-2 bg-slate-500/[0.08] border border-slate-500/25 text-text-secondary text-xs rounded-lg px-4 py-3">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-slate-400" />
              <span>
                <strong>Not graded:</strong>{' '}
                {data.excluded_followers.map((e: any) => `${e.name} (${e.status})`).join(', ')}
                {' — '}the engine doesn&apos;t copy to these, so any trading on them is their own
                book, not a mirror. Including them would put fills the master never made into
                the match rate.
              </span>
            </div>
          )}

          {/* --------------------------------------------- reconciliation */}
          <div className="card-premium p-5">
            <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-1">
              Symbol / side reconciliation
            </h3>
            <p className="text-[11px] text-text-muted mb-3 leading-relaxed">
              <strong className="text-text-secondary">This is the verdict.</strong> NET position
              per symbol — buys and sells offset. That way a laddered exit is judged as one exit
              rather than rung by rung, and a round trip (sell 57, buy back 28, hold 29) is not
              mistaken for over-filling. The wasted turnover is real though, so it is judged
              separately as <strong className="text-text-secondary">excess churn</strong> — which
              is what a duplicate order looks like from the outside.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                    <th className="py-2.5">Follower</th>
                    <th>Symbol</th>
                    <th className="text-center">Verdict</th>
                    <th className="text-right">Master net</th>
                    <th className="text-right">Rungs</th>
                    <th className="text-right">Target net</th>
                    <th className="text-right">Follower net</th>
                    <th className="text-right" title="Total lots traded, both directions">Gross</th>
                    <th className="text-right" title="Lots round-tripped beyond the master">Excess churn</th>
                    <th className="pl-4">Note</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04] font-medium">
                  {(data.groups || []).length === 0 && (
                    <tr><td colSpan={10} className="py-8 text-center text-text-muted">
                      Nothing to reconcile on this day.
                    </td></tr>
                  )}
                  {(data.groups || []).map((g: any) => (
                    <tr key={`${g.account_id}-${g.symbol}`} className="hover:bg-bg-panel/40">
                      <td className="py-2.5 font-semibold text-text-primary">{g.account_name}</td>
                      <td className="font-mono text-text-primary">{g.symbol}</td>
                      <td className="text-center"><VerdictBadge v={g.verdict} /></td>
                      <td className={`text-right font-mono ${g.master_net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {signed(g.master_net)}
                      </td>
                      <td className="text-right font-mono text-text-secondary">
                        {g.master_orders}
                        {g.laddered && (
                          <span className="ml-1 text-[9px] font-bold text-indigo-300"
                            title="Laddered — the master split this across several orders">
                            LDR
                          </span>
                        )}
                      </td>
                      <td className="text-right font-mono text-text-secondary" title={g.target_basis || ''}>
                        {signed(g.target_net)}
                      </td>
                      <td className={`text-right font-mono font-semibold ${g.follower_net >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {signed(g.follower_net)}
                      </td>
                      <td className="text-right font-mono text-text-muted">{lots(g.follower_gross)}</td>
                      <td className={`text-right font-mono ${g.churn_flag ? 'text-amber-400 font-bold' : 'text-text-muted'}`}
                        title={g.churn_note || ''}>
                        {g.churn_flag ? lots(g.excess_churn_lots) : '—'}
                      </td>
                      <td className="pl-4 text-text-muted">
                        {g.note || '—'}
                        {g.churn_flag && (
                          <span className="block text-amber-400/80 mt-0.5">{g.churn_note}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* --------------------------------------------- per follower */}
          <div className="card-premium p-5">
            <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-3">
              Per follower
            </h3>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                    <th className="py-2.5">Follower</th>
                    <th className="text-right">Ratio</th>
                    <th className="text-right" title="Symbol/side groups compared">Groups</th>
                    <th className="text-right">Matched</th>
                    <th className="text-right">Errors</th>
                    <th className="text-right">Lots</th>
                    <th className="text-right">Median</th>
                    <th className="text-right">Avg</th>
                    <th className="text-right">Max</th>
                    <th className="pl-4">Breakdown</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04] font-medium">
                  {(s.per_follower || []).length === 0 && (
                    <tr><td colSpan={10} className="py-8 text-center text-text-muted">
                      No follower accounts configured.
                    </td></tr>
                  )}
                  {(s.per_follower || []).map((f: any) => (
                    <tr key={f.account_id} className="hover:bg-bg-panel/40">
                      <td className="py-2.5 font-semibold text-text-primary">
                        {f.account_name}
                        {f.unreadable && <span className="ml-2"><VerdictBadge v="unreadable" /></span>}
                      </td>
                      <td className="text-right font-mono text-text-secondary">
                        {f.ratio === null ? '—' : f.ratio.toFixed(4)}
                      </td>
                      <td className="text-right font-mono text-text-secondary">{f.groups}</td>
                      <td className={`text-right font-mono font-bold ${
                        f.match_rate_pct === null ? 'text-text-muted'
                          : f.match_rate_pct >= 100 ? 'text-emerald-400' : 'text-rose-400'
                      }`}>
                        {f.match_rate_pct === null ? '—' : `${f.match_rate_pct}%`}
                      </td>
                      <td className={`text-right font-mono font-bold ${f.errors ? 'text-rose-400' : 'text-emerald-400'}`}>
                        {f.errors}
                      </td>
                      <td className="text-right font-mono text-text-secondary">{lots(f.filled_lots)}</td>
                      <td className="text-right font-mono text-text-secondary">{ms(f.median_delay_ms)}</td>
                      <td className="text-right font-mono text-text-secondary">{ms(f.avg_delay_ms)}</td>
                      <td className="text-right font-mono text-text-secondary">{ms(f.max_delay_ms)}</td>
                      <td className="pl-4">
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

          {/* --------------------------------------------- order comparison */}
          <div className="card-premium p-5">
            <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em]">
                Order detail
                <span className="ml-2 normal-case font-medium text-text-muted/70 tracking-normal">
                  supporting rows — the verdict is the table above
                </span>
              </h3>
              <div className="flex items-center gap-1 bg-bg-panel border border-bg-border rounded-lg p-0.5">
                {([
                  ['all', `All (${rows.length})`],
                  ['mismatch', `Unmatched (${rows.filter(r => MISMATCH.has(r.status)).length})`],
                  ['matched', `Matched (${rows.filter(r => r.status === 'matched').length})`],
                ] as const).map(([k, label]) => (
                  <button key={k} onClick={() => setFilter(k)}
                    className={`px-2.5 py-1 rounded-md text-[11px] font-semibold transition-colors ${
                      filter === k ? 'bg-blue-500/20 text-blue-300' : 'text-text-muted hover:text-text-secondary'
                    }`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs border-collapse">
                <thead>
                  <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                    <th className="py-2.5 pl-1 w-6" />
                    <th>Time (IST)</th>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th className="text-right">Master lots</th>
                    <th className="text-right">Master px</th>
                    <th className="text-center">Followers</th>
                    <th className="text-right">Delay</th>
                    <th className="text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04] font-medium">
                  {shown.length === 0 && (
                    <tr><td colSpan={9} className="py-12 text-center text-text-muted">
                      {rows.length === 0
                        ? 'No master fills on this day.'
                        : 'Nothing matches this filter.'}
                    </td></tr>
                  )}
                  {shown.map((r) => {
                    const open = expanded.has(r.master_order_id);
                    const buy = r.side === 'buy';
                    const delays = (r.legs || [])
                      .map((l: any) => l.delay_ms)
                      .filter((d: any) => d !== null && d !== undefined);
                    const worstDelay = delays.length ? Math.max(...delays.map(Math.abs)) : null;
                    const shownDelay = delays.length
                      ? delays.reduce((a: number, b: number) => Math.abs(a) > Math.abs(b) ? a : b)
                      : null;
                    return (
                      <React.Fragment key={r.master_order_id}>
                        <tr onClick={() => toggle(r.master_order_id)}
                          className="hover:bg-bg-panel/40 cursor-pointer">
                          <td className="py-2.5 pl-1">
                            <ChevronDown className={`w-3.5 h-3.5 text-text-muted transition-transform ${open ? '' : '-rotate-90'}`} />
                          </td>
                          <td className="font-mono text-text-secondary">{clock(r.master_first_fill_at)}</td>
                          <td className="font-mono font-semibold text-text-primary">{r.symbol}</td>
                          <td>
                            <span className={`inline-flex items-center gap-1 font-bold ${buy ? 'text-emerald-400' : 'text-rose-400'}`}>
                              {buy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                              {(r.side || '').toUpperCase()}
                            </span>
                          </td>
                          <td className="text-right font-mono text-text-primary">
                            {lots(r.master_lots)}
                            {r.master_fill_count > 1 && (
                              <span className="text-text-muted text-[10px] ml-1" title={`${r.master_fill_count} fills`}>
                                ×{r.master_fill_count}
                              </span>
                            )}
                          </td>
                          <td className="text-right font-mono text-text-secondary">{px(r.master_avg_price)}</td>
                          <td className="text-center">
                            <div className="inline-flex flex-wrap gap-1 justify-center">
                              {(r.legs || []).map((l: any) => (
                                <span key={l.account_id} title={`${l.account_name}: ${l.note || VERDICT[l.verdict]?.help || ''}`}>
                                  <VerdictBadge v={l.verdict} />
                                </span>
                              ))}
                              {!(r.legs || []).length && <span className="text-text-muted">—</span>}
                            </div>
                          </td>
                          <td className={`text-right font-mono ${
                            worstDelay !== null && worstDelay > 30_000 ? 'text-amber-400' : 'text-text-secondary'
                          }`}>
                            {ms(shownDelay)}
                          </td>
                          <td className="text-center"><VerdictBadge v={r.status} /></td>
                        </tr>

                        {open && (
                          <tr className="bg-bg-secondary/40">
                            <td colSpan={9} className="px-4 py-3">
                              <div className="text-[10px] text-text-muted font-mono mb-2">
                                master order <span className="text-text-secondary">{r.master_order_id}</span>
                                {r.order_type && <> · {r.order_type}</>}
                                {r.master_last_fill_at !== r.master_first_fill_at && (
                                  <> · filled {clock(r.master_first_fill_at)} → {clock(r.master_last_fill_at)}</>
                                )}
                              </div>
                              <table className="w-full text-left text-[11px]">
                                <thead>
                                  <tr className="text-text-muted uppercase font-bold text-[9px]">
                                    <th className="py-1.5">Follower</th>
                                    <th className="text-center">Verdict</th>
                                    <th className="text-right">Filled</th>
                                    <th className="text-right">Target</th>
                                    <th className="text-right">Price</th>
                                    <th className="text-right">Delay</th>
                                    <th className="text-right">Placed in</th>
                                    <th className="text-center">Link</th>
                                    <th className="pl-3">Note</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-white/[0.04]">
                                  {(r.legs || []).map((l: any) => (
                                    <tr key={l.account_id}>
                                      <td className="py-1.5 font-semibold text-text-primary">{l.account_name}</td>
                                      <td className="text-center"><VerdictBadge v={l.verdict} /></td>
                                      <td className="text-right font-mono text-text-primary">{lots(l.filled_lots)}</td>
                                      <td className="text-right font-mono text-text-secondary" title={l.target_basis || ''}>
                                        {lots(l.target_lots)}
                                      </td>
                                      <td className="text-right font-mono text-text-secondary">{px(l.avg_price)}</td>
                                      <td className="text-right font-mono text-text-secondary">{ms(l.delay_ms)}</td>
                                      <td className="text-right font-mono text-text-muted"
                                        title="How long the engine took to get the order onto the exchange">
                                        {ms(l.place_latency_ms)}
                                      </td>
                                      <td className="text-center">
                                        {l.link === 'linked' ? (
                                          <span title="A recorded copy ties the master order to this follower order."
                                            className="inline-flex items-center gap-1 text-emerald-400/80">
                                            <Link2 className="w-3 h-3" /> linked
                                          </span>
                                        ) : l.link === 'inferred' ? (
                                          <span title="No copy record linked these — matched on symbol, side and timing. The fill happened; the engine did not write it down."
                                            className="inline-flex items-center gap-1 text-amber-400/90">
                                            <HelpCircle className="w-3 h-3" /> inferred
                                          </span>
                                        ) : <span className="text-text-muted">—</span>}
                                      </td>
                                      <td className="pl-3 text-text-muted">{l.note || l.leg_reason || '—'}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* --------------------------------------------- unexplained fills */}
          {(data.unmatched_follower_fills || []).length > 0 && (
            <div className="card-premium p-5">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-1">
                Unexplained follower fills ({data.unmatched_follower_fills.length})
              </h3>
              <p className="text-[11px] text-text-muted mb-3 leading-relaxed">
                Follower executions on a symbol and side the master never traded today. Not
                automatically wrong — a mirror of an order placed just before the window opened
                lands here — but a follower trading its own book looks exactly the same, which is
                why these are listed rather than counted as agreement. Fills on a symbol the
                master <em>did</em> trade are accounted for by the reconciliation above, ladder
                cover orders included.
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs border-collapse">
                  <thead>
                    <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px]">
                      <th className="py-2.5">Time (IST)</th>
                      <th>Follower</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th className="text-right">Lots</th>
                      <th className="text-right">Price</th>
                      <th>Order id</th>
                      <th>Explanation</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04] font-medium">
                    {data.unmatched_follower_fills.map((u: any) => (
                      <tr key={`${u.account_id}-${u.follower_order_id}`} className="hover:bg-bg-panel/40">
                        <td className="py-2.5 font-mono text-text-secondary">{clock(u.first_fill_at)}</td>
                        <td className="font-semibold text-text-primary">{u.account_name}</td>
                        <td className="font-mono text-text-primary">{u.symbol}</td>
                        <td className={`font-bold ${u.side === 'buy' ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {(u.side || '').toUpperCase()}
                        </td>
                        <td className="text-right font-mono text-text-primary">{lots(u.lots)}</td>
                        <td className="text-right font-mono text-text-secondary">{px(u.avg_price)}</td>
                        <td className="font-mono text-text-muted">{u.follower_order_id}</td>
                        <td className="text-text-muted">{u.explanation}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* --------------------------------------------- how to read this */}
          <div className="card-premium p-5">
            <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.12em] mb-3">
              How to read this
            </h3>
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
              <strong className="text-text-secondary">Delay</strong> is the follower's first fill
              minus the master's. A negative value means the follower traded first — both accounts
              rest a limit at the same price, so that is normal, not an error.
              Sizes are compared against each follower's <strong className="text-text-secondary">proportional
              target</strong>, never the master's raw lots: a follower at 1/40th of the master is
              correct when it fills 1 lot against 40. The
              <strong className="text-text-secondary"> match rate and error count are group-level</strong> —
              per symbol and side, not per order — because a laddered exit is one decision spread
              across many rungs, and counting rungs turns a correctly-tracking follower into a
              page of false misses. Only accounts the engine actually copies to are graded.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
