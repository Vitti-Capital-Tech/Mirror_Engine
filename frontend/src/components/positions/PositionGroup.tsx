'use client';
import React, { useState } from 'react';
import { followerRungs } from '@/context/AccountScopeContext';
import { ChevronDown, Crown, AlertTriangle } from 'lucide-react';
import { AccountPositionCard } from './AccountPositionCard';
import { useAccountLiveView } from '@/hooks/usePositions';

/**
 * One master and its copies, as a single collapsible block.
 *
 * The page used to render two flat lists — "Master Account" and "Follower
 * Accounts (2)" — each a stack of full-height Delta tables. Three problems:
 *
 *   * With several masters under one login, a flat "Follower Accounts" list
 *     mixes copies belonging to different masters into one heading. Which copy
 *     mirrors which master, the single most important fact on this page, was
 *     nowhere on it.
 *   * Every card was expanded, every card fetched its own live view every four
 *     seconds, and one account's tables filled the screen. Comparing a master
 *     against its copy meant scrolling between two things that could not be on
 *     screen together.
 *   * So the comparison this page exists for — same symbols, same direction,
 *     proportionate size — could not actually be made by looking.
 *
 * So: one block per group, and each account inside it collapsed to a single
 * line carrying what you would otherwise expand to find. The full Delta tables
 * are still one click away, and are not mounted (so not polled) until asked
 * for.
 *
 * TWO money columns, not one. Unrealised is what the open legs are worth right
 * now; today's P&L is what the account has actually made since the session
 * began, closed trades included. A position that is 40 down on the day can
 * still show green unrealised, and showing only the second number tells you
 * the position is fine while the account is not.
 */

const money = (v: number) =>
  v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signed = (v: number) => `${v > 0 ? '+' : ''}${money(v)}`;
const pnlClass = (v: number) =>
  v > 0 ? 'text-emerald-400' : v < 0 ? 'text-rose-400' : 'text-text-muted';

/** Shared column widths — the header row on the page renders its labels
 *  against these, which is the only reason the labels sit over their
 *  columns. */
const MAX_MISSING = 3;

export const PCOL = {
  // Wide enough to spell the group labels out. "Grp realised" saved twelve
  // pixels and made the reader decode an abbreviation on a column of money.
  balance: 'w-[4.75rem] sm:w-[6.75rem] shrink-0 text-right',
  realised: 'w-[4.75rem] sm:w-[6.75rem] shrink-0 text-right',
  unreal: 'w-[4.75rem] sm:w-[6.75rem] shrink-0 text-right',
  today: 'w-[4.75rem] sm:w-[6.75rem] shrink-0 text-right',
};

function AccountRow({ acc, legs, missing, isMaster, defaultOpen, rung = null }: {
  acc: any; legs: any[]; missing: string[]; isMaster: boolean; defaultOpen: boolean;
  // Which rung of the stop-loss ladder this follower sits on, or null for a
  // master and for a paused follower (which holds no rung -- see followerRungs).
  rung?: number | null;
}) {
  const [open, setOpen] = useState(defaultOpen);

  // ONE SNAPSHOT, not two.
  //
  // The chips and the total used to come from GET /api/positions while the
  // table directly underneath them came from the per-account live view. Two
  // endpoints, each computing the unrealised figure from whatever it read from
  // Delta at its own moment — so for a second or two after every refresh the
  // row and the table under it showed different numbers for the same position.
  // Matching the poll intervals narrows that window but cannot close it: they
  // are still two requests landing at two times.
  //
  // This is the same query key the card below uses, so React Query serves both
  // from one cache entry. One request, one snapshot, and the row cannot
  // disagree with its own table. Collapsed, the card is not mounted and there
  // is nothing to disagree with, so the list figures are used instead.
  const { data: live } = useAccountLiveView(acc.id, open);
  const liveLegs = React.useMemo(() => {
    if (!open || !live?.risk) return null;
    return live.risk
      .filter((p: any) => Number(p.size) !== 0)
      .map((p: any) => {
        const size = Number(p.size) || 0;
        const cv = Number(p.product?.contract_value ?? 0.001) || 0.001;
        const entry = Number(p.entry_price);
        const mark = Number(p.mark_price);
        const pnl = Number.isFinite(entry) && Number.isFinite(mark)
          ? size * cv * (mark - entry)
          : Number(p.unrealized_pnl || 0);
        return {
          id: p.product_id ?? p.product_symbol,
          symbol: p.product_symbol,
          side: size >= 0 ? 'long' : 'short',
          unrealized_pnl: pnl,
        };
      });
  }, [open, live]);

  const shown = liveLegs ?? legs;
  const unreal = shown.reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);
  // today_pnl is realised + unrealised. The realised half is stored on its
  // own now, so the three columns can read as closed + open = day.
  //
  // Today is SUMMED FROM THE TWO FIGURES ON SCREEN rather than read from
  // today_pnl, which is the same number but computed from the backend's sync
  // snapshot — while the unrealised beside it comes from the fresher live
  // view. Printing the stored one left the row visibly failing to add up by a
  // few paise, which on a column of money reads as a bug whichever number is
  // "right". Summing what is displayed makes the identity true by
  // construction, and makes today's figure as live as the mark it is based on.
  const realised = Number(acc.realized_pnl_today || 0);
  const today = realised + unreal;

  return (
    <div className={isMaster ? '' : 'border-t border-bg-border'}>
      {/* Wraps rather than squeezes. The name does not shrink, so on a phone
          the symbol chips used to overflow straight through the money on the
          right — two numbers printed on top of each other. The chips take a
          line of their own below the name instead. */}
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2 px-4 sm:px-5 py-3 text-left hover:bg-bg-secondary transition-colors">
        <ChevronDown className={`w-3.5 h-3.5 shrink-0 text-text-muted transition-transform ${open ? '' : '-rotate-90'}`} />

        <span className="min-w-0 flex items-center gap-2 shrink-0">
          {isMaster && <Crown className="w-3.5 h-3.5 text-amber-400/90 shrink-0" />}
          <span className={`truncate ${isMaster
            ? 'text-[13px] font-semibold text-text-primary'
            : 'text-[12.5px] font-semibold text-text-secondary'}`}>
            {acc.name}
          </span>
          {/* A bordered chip, not bare text. Loose uppercase letters beside a
              name read as something that fell off another element. */}
          {isMaster
            ? <span className="shrink-0 inline-flex items-center rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-amber-300/90">Master</span>
            : <span className="shrink-0 inline-flex items-center rounded border border-bg-border bg-bg-primary/60 px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-text-muted"
                title={rung
                  ? `Follower ${rung} — its stop-loss and take-profit sit ${rung}x the master's SL/TP offset beyond his, so it exits after him and after any follower ahead of it.`
                  : 'Paused — it holds no place in the stop-loss ladder while it is not running.'}>
                Follower{rung ? ` ${rung}` : ''}
              </span>}
        </span>

        {/* What it is holding, without expanding anything. */}
        <span className="order-last sm:order-none basis-full sm:basis-auto sm:flex-1 min-w-0 flex items-center gap-1.5 flex-wrap sm:flex-nowrap overflow-hidden pl-6 sm:pl-0">
          {/* Warnings come FIRST. Putting them after the ordinary chips meant
              that on a busy account the clipping ate the one chip the row
              exists to surface — a copy missing a leg its master holds. */}
          {missing.slice(0, MAX_MISSING).map((sym) => (
            <span key={sym} title={`The master holds ${sym} and this follower does not`}
              className="shrink-0 inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
              <AlertTriangle className="w-3 h-3" /> no {sym}
            </span>
          ))}
          {missing.length > MAX_MISSING && (
            <span className="shrink-0 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
              +{missing.length - MAX_MISSING} more missing
            </span>
          )}
          {/* No per-symbol chips. They restated the table below — same
              symbols, same sides, same figures, in less detail — and open is
              how the page starts. What stays is the thing a table cannot
              show: a leg the master holds and this follower does not. */}
          {!open && shown.length === 0 && (
            <span className="text-[11px] text-text-muted">Nothing open</span>
          )}
        </span>

        {/* Three figures do not fit beside a name on a phone, so there they
            take a line of their own rather than crushing it. */}
        <span className="basis-full sm:basis-auto ml-auto sm:ml-0 shrink-0 flex items-center justify-end gap-3 sm:gap-6 pl-6 sm:pl-0">
          <span className={PCOL.balance}>
            {/* On the number itself, always. A single header at the top of
                the page stops explaining anything once the tables are open —
                by the third account the labels are hundreds of pixels away,
                and a bare 7,450.24 beside +132.88 says nothing about which is
                which. */}
            <span className="block text-[8.5px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5">Balance</span>
            <span className="text-[12.5px] font-mono text-text-secondary">
              {acc.balance != null ? money(Number(acc.balance)) : '—'}
            </span>
          </span>
          <span className={PCOL.realised}>
            <span className="block text-[8.5px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5">Realised</span>
            <span className={`text-[12.5px] font-mono ${pnlClass(realised)}`}>{signed(realised)}</span>
          </span>
          <span className={PCOL.unreal}>
            <span className="block text-[8.5px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5">Unreal.</span>
            <span className={`text-[12.5px] font-mono ${pnlClass(unreal)}`}>
              {shown.length ? signed(unreal) : '—'}
            </span>
          </span>
          <span className={PCOL.today}>
            <span className="block text-[8.5px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5">Today</span>
            <span className={`text-[12.5px] font-mono font-bold ${pnlClass(today)}`}>{signed(today)}</span>
          </span>
        </span>
      </button>

      {/* Open by default: the tables are what the page is for, and having to
          click into each account to see them was a click per account on every
          visit. A collapsed account is still not mounted, so collapsing one
          does stop it polling — it is just no longer the starting state. */}
      {open && (
        <div className="px-3 sm:px-4 py-3 bg-bg-primary border-t border-bg-border">
          <AccountPositionCard acc={acc} compact />
        </div>
      )}
    </div>
  );
}

export function PositionGroup({ group, positions, showOwner = false, defaultOpen = true }: {
  group: { groupId: string; master?: any; followers: any[]; label: string };
  positions: any[];
  showOwner?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const { master, followers } = group;
  // Same ladder the accounts page shows, from the same helper, so the two
  // screens can never disagree about which follower is which.
  const rungs = followerRungs(followers);
  const email = master?.owner_email || followers[0]?.owner_email;

  const legsOf = (id: string) => positions.filter((p) => String(p.account_id) === String(id));
  const masterLegs = master ? legsOf(master.id) : [];
  const masterSymbols = masterLegs.map((p) => p.symbol);

  const members = [...(master ? [master] : []), ...followers];
  const groupLegs = members.flatMap((a) => legsOf(a.id));
  const groupUnreal = groupLegs.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
  // Same identity at group level, for the same reason.
  const groupRealised = members.reduce((s, a) => s + Number(a.realized_pnl_today || 0), 0);
  const groupToday = groupRealised + groupUnreal;
  const groupBalance = members.reduce((s, a) => s + Number(a.balance || 0), 0);

  // A copy that is missing something its master holds is the failure this desk
  // actually cares about, and it is invisible in a list of per-account tables.
  const missingFor = (acc: any) => {
    if (!master || acc.id === master.id) return [];
    const held = new Set(legsOf(acc.id).map((p) => p.symbol));
    return masterSymbols.filter((s) => !held.has(s));
  };
  const anyMissing = followers.some((f) => missingFor(f).length > 0);

  return (
    <div className="rounded-2xl border border-bg-border bg-bg-panel shadow-lg shadow-black/30">
      {/* Stacks on a phone. Side by side, the two money columns left the
          master's name about ten pixels of room — the group was identified by
          a truncated badge and nothing else. */}
      <div className="flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2 px-4 sm:px-5 py-3.5 bg-bg-elevated rounded-t-2xl border-b border-bg-border">
        <button type="button" onClick={() => setOpen((o) => !o)}
          title={open ? 'Collapse' : 'Expand'}
          className="p-1 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-secondary/60 transition-colors shrink-0">
          <ChevronDown className={`w-4 h-4 transition-transform ${open ? '' : '-rotate-90'}`} />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-bold text-text-primary truncate">
              {master?.name || group.label}
            </span>
            {anyMissing && (
              <span className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-amber-300">
                <AlertTriangle className="w-2.5 h-2.5" /> Mismatch
              </span>
            )}
          </div>
          <div className="text-[11px] text-text-muted mt-0.5 truncate">
            {showOwner && email ? <>{email} · </> : null}
            {members.length} account{members.length === 1 ? '' : 's'}
            {' · '}
            {groupLegs.length} open {groupLegs.length === 1 ? 'position' : 'positions'}
          </div>
        </div>

        <div className="basis-full sm:basis-auto shrink-0 flex items-center justify-end gap-3 sm:gap-6 pl-8 sm:pl-0">
          {/* The page labels the COLUMNS once, above. This labels the SCOPE,
              which is a different thing and the one that was missing: expanded,
              these totals sat directly on top of the master's own pair with
              nothing to say which was which. */}
          <div className={PCOL.balance}>
            <div className="text-[8px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5 whitespace-nowrap">Group balance</div>
            <div className="text-[13px] font-mono font-bold text-text-primary">{money(groupBalance)}</div>
          </div>
          <div className={PCOL.realised}>
            <div className="text-[8px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5 whitespace-nowrap">Group realised</div>
            <div className={`text-[13px] font-mono font-bold ${pnlClass(groupRealised)}`}>{signed(groupRealised)}</div>
          </div>
          <div className={PCOL.unreal}>
            <div className="text-[8px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5 whitespace-nowrap">Group unreal.</div>
            <div className={`text-[13px] font-mono font-bold ${pnlClass(groupUnreal)}`}>
              {groupLegs.length ? signed(groupUnreal) : '—'}
            </div>
          </div>
          <div className={PCOL.today}>
            <div className="text-[8px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5 whitespace-nowrap">Group today</div>
            <div className={`text-[13px] font-mono font-bold ${pnlClass(groupToday)}`}>{signed(groupToday)}</div>
          </div>
        </div>
      </div>

      {open && (
        <div className="rounded-b-2xl">
          {master
            ? <AccountRow acc={master} legs={masterLegs} missing={[]} isMaster defaultOpen />
            : (
              <div className="px-5 py-4 text-[12px] text-text-muted">
                No active master in this group — nothing is being copied.
              </div>
            )}
          {followers.map((f) => (
            <AccountRow key={f.id} acc={f} legs={legsOf(f.id)} missing={missingFor(f)}
              isMaster={false} defaultOpen rung={rungs[String(f.id)] ?? null} />
          ))}
          {followers.length === 0 && master && (
            <div className="px-5 py-3 border-t border-bg-border text-[11.5px] text-text-muted">
              No followers — this master is trading on its own.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
