'use client';
import React, { useState } from 'react';
import {
  usePauseAccount, useResumeAccount, useDeleteAccount,
  useTestAccount, usePromoteAccount,
} from '@/hooks/useAccounts';
import { EditAccountModal } from './EditAccountModal';
import {
  ChevronDown, Crown, Plus, Pencil, Trash2, ShieldCheck,
  RefreshCw, MoreHorizontal,
} from 'lucide-react';
import toast from 'react-hot-toast';

/**
 * One master and the copies assigned to it, as a single card.
 *
 * The page used to render each group as TWO tables — "MASTER ACCOUNT" and
 * "FOLLOWER ACCOUNTS" — each repeating the full column header for, usually, one
 * row. Then it became cards, which fixed the relationship but left three things
 * unresolved that this pass deals with:
 *
 *   * NOTHING LINED UP. Each row printed its own "BALANCE" / "TODAY" labels
 *     above its own numbers, so the labels appeared six times on a page of six
 *     accounts and the figures sat at different x positions on the master row
 *     and the copy rows — a column of money you cannot run your eye down is not
 *     a column. The widths are now fixed and shared, and the labels are printed
 *     once, at the top of the page.
 *   * THE STATE WAS SAID THREE TIMES AND CHANGED NOWHERE. A row carried a
 *     "MASTER" chip, a "LIVE" chip and an "Active" chip, which read as three
 *     different statuses, while actually pausing the account was buried in a
 *     menu. One switch now both shows whether an account is running and is how
 *     you stop it.
 *   * "LIVE" was noise. Nearly everything is live; what needs pointing out is
 *     the account that is NOT, so only DEMO is marked.
 */

/* Shared column widths. The page header renders its labels against these same
   constants, which is the only reason the labels sit over their columns. */
export const COL = {
  rail: 'w-7 shrink-0',
  // Columns drop away as the screen narrows rather than squeezing the name
  // out of existence — at phone width the fixed widths left about twenty
  // pixels for the account name. What they hold reappears under the name.
  mid: 'hidden lg:flex items-center w-44 shrink-0 min-w-0',
  balance: 'hidden sm:flex items-center justify-end w-32 shrink-0',
  pnl: 'hidden sm:flex items-center justify-end w-28 shrink-0',
  tail: 'w-[5.5rem] shrink-0',
};

function confirmToast(message: string, onConfirm: () => void) {
  toast((t) => (
    <div className="flex flex-col gap-2">
      <span className="text-text-primary">{message}</span>
      <div className="flex gap-2 justify-end">
        <button onClick={() => toast.dismiss(t.id)}
          className="px-2.5 py-1 text-xs rounded-md bg-bg-secondary text-text-secondary hover:text-white border border-bg-border">
          Cancel
        </button>
        <button onClick={() => { toast.dismiss(t.id); onConfirm(); }}
          className="px-2.5 py-1 text-xs rounded-md bg-rose-500 hover:bg-rose-400 text-white font-semibold">
          Confirm
        </button>
      </div>
    </div>
  ), { duration: 8000 });
}

// Always two decimals. A column mixing 15,200 and 7,450.24 cannot be scanned
// down — the eye has nothing to align on.
const money = (v: any) =>
  v == null || v === ''
    ? '—'
    : Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const pnlClass = (v: number) =>
  v > 0 ? 'text-emerald-400' : v < 0 ? 'text-rose-400' : 'text-text-muted';

const signed = (v: any) => `${Number(v || 0) > 0 ? '+' : ''}${money(v)}`;

function allocationText(a: any) {
  const v = Number(a.allocation_value || 0);
  switch (a.allocation_mode) {
    case 'auto_ratio': return 'Matches balance';
    case 'fixed': return `Fixed ${v}`;
    case 'multiplier': return `${v}× the master`;
    case 'capital_pct': return `${v}% of capital`;
    default: return '—';
  }
}

/** The running state in words, next to the name it belongs to.
 *
 *  The switch on the right is how you change it, but a switch alone asks the
 *  reader to decode a position — left or right, on or off — before they know
 *  whether their money is being copied. The word says it outright. */
function RunState({ acc }: { acc: any }) {
  const running = acc?.status === 'active';
  return (
    <span className={`inline-flex items-center gap-1.5 shrink-0 text-[10.5px] font-semibold ${
      running ? 'text-emerald-400' : 'text-amber-400'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${running ? 'bg-emerald-400' : 'bg-amber-400'}`} />
      {running ? 'Live' : 'Paused'}
    </span>
  );
}

/** Only the exception is marked. Nearly every account trades the real
 *  exchange, so a badge on all of them carried no information.
 *
 *  Called TESTNET, not DEMO, so it cannot be read as the opposite of the
 *  "Live" above — one is which exchange, the other is whether it is running,
 *  and an account can be a paused testnet account or a live running one. */
function TestnetTag({ acc }: { acc: any }) {
  if (!acc || acc.environment === 'live') return null;
  return (
    <span className="shrink-0 text-[9px] font-bold tracking-wider rounded px-1.5 py-px border text-slate-300 border-slate-500/30 bg-slate-500/10">
      TESTNET
    </span>
  );
}

/** Running or paused, as a switch rather than a label.
 *
 *  It replaces both the "Active" chip and the "Pause copying" menu item: the
 *  chip said what was happening but could not change it, and the menu item
 *  could change it but was two clicks and out of sight. One control that shows
 *  the state AND is the way to change it is less to read and less to learn. */
function RunSwitch({ acc }: { acc: any }) {
  const pause = usePauseAccount();
  const resume = useResumeAccount();
  const running = acc.status === 'active';
  const busy = pause.isPending || resume.isPending;

  const toggle = () => {
    const m = running ? pause : resume;
    m.mutate(acc.id, {
      onSuccess: () => toast.success(`“${acc.name}” ${running ? 'paused' : 'resumed'}.`),
      onError: (e: any) => toast.error(e.message || 'Could not change that.'),
    });
  };

  return (
    <button
      type="button" role="switch" aria-checked={running} disabled={busy}
      onClick={toggle}
      aria-label={running ? `Pause ${acc.name}` : `Resume ${acc.name}`}
      title={running ? 'Running — click to pause' : 'Paused — click to resume'}
      className={`relative inline-flex h-[18px] w-8 shrink-0 items-center rounded-full border transition-colors disabled:opacity-50 ${
        running
          ? 'bg-emerald-500/25 border-emerald-500/40'
          : 'bg-bg-primary border-bg-border hover:border-text-muted'}`}
    >
      <span className={`absolute h-3 w-3 rounded-full transition-all ${
        running ? 'left-[15px] bg-emerald-400' : 'left-[2px] bg-text-muted'}`} />
    </button>
  );
}

/** Row actions, in a menu rather than five identical ghost icons in a row.
 *  Pause is no longer in here — it has its own switch on the row. */
function RowActions({ acc, onEdit, onTest, testing }: any) {
  const [open, setOpen] = useState(false);
  // Which way the menu opens. The last row of the last card is near the bottom
  // of the window, and a menu that always dropped downwards put its most
  // destructive item below the fold.
  const [dropUp, setDropUp] = useState(false);
  const ref = React.useRef<HTMLDivElement>(null);
  const menuRef = React.useRef<HTMLDivElement>(null);

  // Measured, not estimated. The first attempt guessed the menu's height from
  // its item count and still got it wrong, because the page carries a fixed
  // live-log bar along the bottom that innerHeight knows nothing about — the
  // menu "fitted" and Delete was still underneath it. Reserving a gutter for
  // that bar and reading the menu's real box gets it right either way.
  React.useLayoutEffect(() => {
    if (!open || dropUp || !menuRef.current) return;
    const BOTTOM_GUTTER = 96;
    const r = menuRef.current.getBoundingClientRect();
    if (r.bottom > window.innerHeight - BOTTOM_GUTTER) setDropUp(true);
  }, [open, dropUp]);
  const deleteAcc = useDeleteAccount();
  const promoteAcc = usePromoteAccount();

  React.useEffect(() => {
    function out(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', out);
    return () => document.removeEventListener('mousedown', out);
  }, []);

  const item = 'w-full flex items-center gap-2.5 px-3 py-2 text-left text-[12px] hover:bg-bg-secondary/70 transition-colors';

  return (
    <div className="relative" ref={ref}>
      <button type="button" title="More actions"
        onClick={() => { setDropUp(false); setOpen((o) => !o); }}
        className="p-1.5 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-secondary/60 transition-colors">
        <MoreHorizontal className="w-4 h-4" />
      </button>
      {open && (
        <div ref={menuRef}
          className={`absolute right-0 w-48 rounded-xl border border-bg-border bg-bg-panel shadow-2xl z-40 py-1 animate-fade-in ${
            dropUp ? 'bottom-full mb-1' : 'top-full mt-1'}`}>
          <button className={item} onClick={() => { setOpen(false); onTest(acc); }}>
            {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5 text-text-muted" />}
            Test connection
          </button>
          <button className={item} onClick={() => { setOpen(false); onEdit(acc); }}>
            <Pencil className="w-3.5 h-3.5 text-text-muted" /> Edit settings
          </button>
          {!acc.is_master && (
            <button className={item} onClick={() => {
              setOpen(false);
              confirmToast(`Make “${acc.name}” the master? The current master becomes a copy.`, () =>
                promoteAcc.mutate(acc.id, {
                  onSuccess: () => toast.success(`“${acc.name}” is now the master.`),
                  onError: (e: any) => toast.error(e.message || 'Failed.'),
                }));
            }}>
              <Crown className="w-3.5 h-3.5 text-amber-400" /> Make this the master
            </button>
          )}
          <div className="my-1 h-px bg-bg-border" />
          <button className={`${item} text-rose-400`} onClick={() => {
            setOpen(false);
            confirmToast(`Delete “${acc.name}”? This cannot be undone.`, () =>
              deleteAcc.mutate(acc.id, {
                onSuccess: () => toast.success(`“${acc.name}” deleted.`),
                onError: (e: any) => toast.error(e.message || 'Failed.'),
              }));
          }}>
            <Trash2 className="w-3.5 h-3.5" /> Delete account
          </button>
        </div>
      )}
    </div>
  );
}

export function AccountGroupCard({
  group, maxFollowers = 2, onAddCopy, showOwner = false,
}: {
  group: { groupId: string; master?: any; followers: any[]; label: string; ownerId?: string | null };
  maxFollowers?: number;
  onAddCopy?: (masterId: string) => void;
  showOwner?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const [editing, setEditing] = useState<any>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const testAcc = useTestAccount();

  const { master, followers } = group;
  const email = master?.owner_email || followers[0]?.owner_email;
  const full = followers.length >= maxFollowers;
  const paused = master ? master.status !== 'active' : false;

  const onTest = (acc: any) => {
    setTestingId(acc.id);
    const id = toast.loading('Testing connection…');
    testAcc.mutate(acc.id, {
      onSuccess: (r: any) => toast.success(r?.message || 'Connection OK.', { id }),
      onError: (e: any) => toast.error(e.message || 'Connection failed.', { id }),
      onSettled: () => setTestingId(null),
    });
  };

  const groupPnl = (master ? Number(master.today_pnl || 0) : 0)
    + followers.reduce((s, f) => s + Number(f.today_pnl || 0), 0);

  const addCopyButton = master && !full && (
    <button type="button" onClick={() => onAddCopy?.(String(master.id))}
      className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-blue-400 hover:text-blue-300 transition-colors">
      <Plus className="w-3.5 h-3.5" /> Add a copy
    </button>
  );

  return (
    /* No overflow-hidden: it clipped the row menus against the card edge, so
       half of "Delete account" simply was not there. The corners are rounded
       on the pieces that touch them instead. */
    <div className={`rounded-2xl border border-bg-border bg-bg-panel/40 transition-opacity ${
      paused ? 'opacity-60' : ''}`}>
      {/* ── Group head: the master ───────────────────────────────── */}
      <div className="flex items-center gap-3 px-4 sm:px-5 py-3.5 bg-bg-secondary/20 rounded-t-2xl">
        <div className={`${COL.rail} flex items-center`}>
          <button type="button" onClick={() => setOpen((o) => !o)}
            title={open ? 'Collapse' : 'Expand'}
            className="p-1 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-secondary/60 transition-colors">
            <ChevronDown className={`w-4 h-4 transition-transform ${open ? '' : '-rotate-90'}`} />
          </button>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Crown className="w-3.5 h-3.5 text-amber-400/90 shrink-0" />
            <span className="text-[14px] font-bold text-text-primary truncate">
              {master?.name || group.label}
            </span>
            {master && <RunState acc={master} />}
            <TestnetTag acc={master} />
          </div>
          <div className="text-[11px] text-text-muted mt-0.5 truncate">
            {showOwner && email ? <>{email} · </> : null}
            {followers.length} of {maxFollowers} {maxFollowers === 1 ? 'copy' : 'copies'}
            {/* An advanced setting, and the first thing to go when the line
                is short — the balance behind it matters more. */}
            {master?.tp_sl_offset
              ? <span className="hidden sm:inline"> · SL/TP offset {master.tp_sl_offset}</span>
              : null}
            {!open && <> · group today <span className={pnlClass(groupPnl)}>{signed(groupPnl)}</span></>}
            <span className="sm:hidden font-mono">
              {' · '}{money(master?.balance)}
              {' · '}<span className={pnlClass(Number(master?.today_pnl || 0))}>{signed(master?.today_pnl)}</span>
            </span>
          </div>
        </div>

        <div className={COL.mid} />
        <div className={`${COL.balance} text-[13px] font-mono font-bold text-text-primary`}>
          {money(master?.balance)}
        </div>
        <div className={`${COL.pnl} text-[13px] font-mono font-bold ${pnlClass(Number(master?.today_pnl || 0))}`}>
          {master ? signed(master.today_pnl) : '—'}
        </div>
        <div className={`${COL.tail} flex items-center justify-end gap-1`}>
          {master && <RunSwitch acc={master} />}
          {master && <RowActions acc={master} onEdit={setEditing} onTest={onTest} testing={testingId === master.id} />}
        </div>
      </div>

      {open && (
        <div className="border-t border-bg-border rounded-b-2xl overflow-visible">
          {followers.length === 0 ? (
            /* The "no copies" note and the add button were two separate bands
               saying one thing. They are one line now. */
            <div className="flex items-center justify-between gap-3 px-5 py-4">
              <span className="text-[12px] text-text-muted">
                {master
                  ? 'No copies yet — this master is trading on its own.'
                  : 'Not attached to a master yet — nothing is being copied.'}
              </span>
              {addCopyButton}
            </div>
          ) : (
            <>
              <div className="divide-y divide-white/[0.04]">
                {followers.map((f, i) => {
                  const last = i === followers.length - 1;
                  const off = f.status !== 'active';
                  return (
                    <div key={f.id}
                      className={`flex items-stretch gap-3 px-4 sm:px-5 hover:bg-bg-secondary/20 transition-colors ${
                        off ? 'opacity-55' : ''}`}>
                      {/* The rail is what says "this one copies the one above"
                          — an indent alone does not. */}
                      <div className={`${COL.rail} relative`} aria-hidden>
                        <span className={`absolute left-3 w-px bg-bg-border top-0 ${last ? 'h-1/2' : 'bottom-0'}`} />
                        <span className="absolute left-3 top-1/2 w-2.5 h-px bg-bg-border" />
                      </div>

                      <div className="min-w-0 flex-1 py-3">
                        <div className="flex items-center gap-2">
                          <span className="text-[13px] font-semibold text-text-primary truncate">{f.name}</span>
                          <RunState acc={f} />
                          <TestnetTag acc={f} />
                        </div>
                        {/* Whatever the width has taken away, shown here instead. */}
                        <div className="lg:hidden text-[11px] text-text-muted mt-0.5 truncate">
                          <span className="sm:hidden font-mono">
                            {money(f.balance)}
                            {' · '}<span className={pnlClass(Number(f.today_pnl || 0))}>{signed(f.today_pnl)}</span>
                            {' · '}
                          </span>
                          {allocationText(f)}
                        </div>
                      </div>

                      <div className={`${COL.mid} py-3 text-[11px] text-text-muted truncate`}>
                        {allocationText(f)}
                      </div>
                      <div className={`${COL.balance} py-3 text-[13px] font-mono text-text-primary`}>
                        {money(f.balance)}
                      </div>
                      <div className={`${COL.pnl} py-3 text-[13px] font-mono ${pnlClass(Number(f.today_pnl || 0))}`}>
                        {signed(f.today_pnl)}
                      </div>
                      <div className={`${COL.tail} py-3 flex items-center justify-end gap-1`}>
                        <RunSwitch acc={f} />
                        <RowActions acc={f} onEdit={setEditing} onTest={onTest} testing={testingId === f.id} />
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Only rendered when there is something to do. A full group
                  already says "2 of 2 copies" in its head; a band repeating it
                  under a dead button was chrome, not information. */}
              {addCopyButton && (
                <div className="px-5 py-2.5 border-t border-bg-border">{addCopyButton}</div>
              )}
            </>
          )}
        </div>
      )}

      {editing && <EditAccountModal account={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
