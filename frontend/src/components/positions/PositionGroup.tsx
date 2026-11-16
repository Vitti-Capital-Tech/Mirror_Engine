'use client';
import React, { useState } from 'react';
import { ChevronDown, Crown, AlertTriangle } from 'lucide-react';
import { AccountPositionCard } from './AccountPositionCard';

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
/** How many symbol chips a row shows before it stops and says how many more
 *  there are. A dozen legs wrapped to three lines, pushed the money out of its
 *  columns onto a line of its own, and stopped being glanceable — which was
 *  the entire reason for showing the symbols up here. Four is what fits beside
 *  a name and two figures without wrapping; the rest are one click away in the
 *  table that already lists them properly. */
const MAX_CHIPS = 4;
const MAX_MISSING = 3;

export const PCOL = {
  balance: 'w-[4.75rem] sm:w-28 shrink-0 text-right',
  today: 'w-[4.75rem] sm:w-28 shrink-0 text-right',
  unreal: 'w-[4.75rem] sm:w-28 shrink-0 text-right',
};

/** A symbol the account is holding, and which way. Direction is the colour of
 *  the rail AND the word, never the colour alone. */
function Leg({ p }: { p: any }) {
  const long = String(p.side).toLowerCase() === 'long';
  const pnl = Number(p.unrealized_pnl || 0);
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-bg-border bg-bg-primary/60 pl-1.5 pr-2 py-0.5">
      <span className={`w-[3px] h-3 rounded-full ${long ? 'bg-emerald-500' : 'bg-rose-500'}`} />
      <span className="text-[10.5px] font-semibold text-text-primary">{p.symbol}</span>
      <span className="text-[9.5px] font-bold uppercase tracking-wide text-text-muted">
        {long ? 'Long' : 'Short'}
      </span>
      <span className={`text-[10.5px] font-mono ${pnlClass(pnl)}`}>{signed(pnl)}</span>
    </span>
  );
}

function AccountRow({ acc, legs, missing, isMaster, defaultOpen }: {
  acc: any; legs: any[]; missing: string[]; isMaster: boolean; defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const unreal = legs.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
  const today = Number(acc.today_pnl || 0);

  return (
    <div className={isMaster ? '' : 'border-t border-bg-border'}>
      {/* Wraps rather than squeezes. The name does not shrink, so on a phone
          the symbol chips used to overflow straight through the money on the
          right — two numbers printed on top of each other. The chips take a
          line of their own below the name instead. */}
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2 px-4 sm:px-5 py-3 text-left hover:bg-bg-secondary/20 transition-colors">
        <ChevronDown className={`w-3.5 h-3.5 shrink-0 text-text-muted transition-transform ${open ? '' : '-rotate-90'}`} />

        <span className="min-w-0 flex items-center gap-2 shrink-0">
          {isMaster && <Crown className="w-3.5 h-3.5 text-amber-400/90 shrink-0" />}
          <span className={`truncate ${isMaster
            ? 'text-[13px] font-semibold text-text-primary'
            : 'text-[12.5px] font-semibold text-text-secondary'}`}>
            {acc.name}
          </span>
          <span className="shrink-0 text-[9px] font-bold uppercase tracking-wider text-text-muted/70">
            {isMaster ? 'Master' : 'Copy'}
          </span>
        </span>

        {/* What it is holding, without expanding anything. */}
        <span className="order-last sm:order-none basis-full sm:basis-auto sm:flex-1 min-w-0 flex items-center gap-1.5 flex-wrap sm:flex-nowrap overflow-hidden pl-6 sm:pl-0">
          {/* Warnings come FIRST. Putting them after the ordinary chips meant
              that on a busy account the clipping ate the one chip the row
              exists to surface — a copy missing a leg its master holds. */}
          {missing.slice(0, MAX_MISSING).map((sym) => (
            <span key={sym} title={`The master holds ${sym} and this copy does not`}
              className="shrink-0 inline-flex items-center gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
              <AlertTriangle className="w-3 h-3" /> no {sym}
            </span>
          ))}
          {missing.length > MAX_MISSING && (
            <span className="shrink-0 rounded-md border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300">
              +{missing.length - MAX_MISSING} more missing
            </span>
          )}
          {legs.length === 0
            ? <span className="text-[11px] text-text-muted">Nothing open</span>
            : legs.slice(0, Math.max(1, MAX_CHIPS - Math.min(missing.length, MAX_MISSING)))
                  .map((p) => <Leg key={p.id} p={p} />)}
          {(() => {
            const shown = Math.max(1, MAX_CHIPS - Math.min(missing.length, MAX_MISSING));
            return legs.length > shown ? (
              <span className="shrink-0 rounded-md border border-bg-border bg-bg-primary/60 px-1.5 py-0.5 text-[10px] font-semibold text-text-muted">
                +{legs.length - shown} more
              </span>
            ) : null;
          })()}
        </span>

        {/* Three figures do not fit beside a name on a phone, so there they
            take a line of their own rather than crushing it. */}
        <span className="basis-full sm:basis-auto ml-auto sm:ml-0 shrink-0 flex items-center justify-end gap-3 sm:gap-6 pl-6 sm:pl-0">
          <span className={PCOL.balance}>
            {/* Labels only on narrow screens, where the column header above the
                groups is not shown. */}
            <span className="sm:hidden block text-[8.5px] uppercase tracking-wider text-text-muted leading-none mb-0.5">Balance</span>
            <span className="text-[12.5px] font-mono text-text-secondary">
              {acc.balance != null ? money(Number(acc.balance)) : '—'}
            </span>
          </span>
          <span className={PCOL.today}>
            <span className="sm:hidden block text-[8.5px] uppercase tracking-wider text-text-muted leading-none mb-0.5">Today</span>
            <span className={`text-[12.5px] font-mono ${pnlClass(today)}`}>{signed(today)}</span>
          </span>
          <span className={PCOL.unreal}>
            <span className="sm:hidden block text-[8.5px] uppercase tracking-wider text-text-muted leading-none mb-0.5">Unreal.</span>
            <span className={`text-[12.5px] font-mono font-bold ${pnlClass(unreal)}`}>
              {legs.length ? signed(unreal) : '—'}
            </span>
          </span>
        </span>
      </button>

      {/* Not mounted until asked for, so a collapsed account is not polling
          Delta every four seconds for tables nobody is reading. */}
      {open && (
        <div className="px-3 sm:px-4 pb-4">
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
  const email = master?.owner_email || followers[0]?.owner_email;

  const legsOf = (id: string) => positions.filter((p) => String(p.account_id) === String(id));
  const masterLegs = master ? legsOf(master.id) : [];
  const masterSymbols = masterLegs.map((p) => p.symbol);

  const members = [...(master ? [master] : []), ...followers];
  const groupLegs = members.flatMap((a) => legsOf(a.id));
  const groupUnreal = groupLegs.reduce((s, p) => s + Number(p.unrealized_pnl || 0), 0);
  const groupToday = members.reduce((s, a) => s + Number(a.today_pnl || 0), 0);
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
    <div className="rounded-2xl border border-bg-border bg-bg-panel/40">
      {/* Stacks on a phone. Side by side, the two money columns left the
          master's name about ten pixels of room — the group was identified by
          a truncated badge and nothing else. */}
      <div className="flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2 px-4 sm:px-5 py-3.5 bg-bg-secondary/20 rounded-t-2xl">
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
            <div className="text-[8.5px] uppercase tracking-wider text-text-muted/80 leading-none mb-0.5">Group</div>
            <div className="text-[13px] font-mono font-bold text-text-primary">{money(groupBalance)}</div>
          </div>
          <div className={PCOL.today}>
            <div className="text-[8.5px] uppercase tracking-wider text-text-muted/80 leading-none mb-0.5">Group</div>
            <div className={`text-[13px] font-mono font-bold ${pnlClass(groupToday)}`}>{signed(groupToday)}</div>
          </div>
          <div className={PCOL.unreal}>
            <div className="text-[8.5px] uppercase tracking-wider text-text-muted/80 leading-none mb-0.5">Group</div>
            <div className={`text-[13px] font-mono font-bold ${pnlClass(groupUnreal)}`}>
              {groupLegs.length ? signed(groupUnreal) : '—'}
            </div>
          </div>
        </div>
      </div>

      {open && (
        <div className="border-t border-bg-border rounded-b-2xl bg-bg-primary/20">
          {master
            ? <AccountRow acc={master} legs={masterLegs} missing={[]} isMaster defaultOpen={false} />
            : (
              <div className="px-5 py-4 text-[12px] text-text-muted">
                No active master in this group — nothing is being copied.
              </div>
            )}
          {followers.map((f) => (
            <AccountRow key={f.id} acc={f} legs={legsOf(f.id)} missing={missingFor(f)}
              isMaster={false} defaultOpen={false} />
          ))}
          {followers.length === 0 && master && (
            <div className="px-5 py-3 border-t border-bg-border text-[11.5px] text-text-muted">
              No copies — this master is trading on its own.
            </div>
          )}
        </div>
      )}
    </div>
  );
}
