'use client';
import React, { useState } from 'react';
import { ChevronDown, User } from 'lucide-react';

/**
 * One account holder, wrapping their master/follower groups.
 *
 * ONLY for an admin viewing every account at once. Picking a single holder
 * from the header switcher gives exactly the view that holder gets, and
 * wrapping one person's groups in a band headed by their own email would be
 * telling them who they are.
 *
 * It exists because "all accounts" was a flat run of group cards whose only
 * clue to ownership was a line of small print inside each header. Two holders'
 * groups sat shoulder to shoulder and read as one desk — which is exactly the
 * misreading that matters here, because the whole point of the engine is that
 * one person's master never reaches another person's followers.
 *
 * Collapsible, because oversight is usually "is anyone's desk on fire", and
 * that is answered by the totals on this bar without opening anything.
 *
 * Its figures are labelled "total …", one step up from a group's "group …"
 * and an account's bare "balance". Three levels of summary stacked on a page
 * all reading "BALANCE" is three numbers that look like the same measurement
 * of different things, when they are different measurements: an account, that
 * account's group, and everything this person holds.
 */
export interface OwnerStat {
  label: string;
  value: string;
  tone?: 'pos' | 'neg' | 'flat';
}

export function OwnerSection({
  email, subtitle, stats = [], status, children, defaultOpen = true,
}: {
  email?: string;
  subtitle: string;
  stats?: OwnerStat[];
  /** Whether anything on this desk needs a human — see DeskHealth. Totals
   *  answer "how much" and never "is it working". */
  status?: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const tone = (t?: string) =>
    t === 'pos' ? 'text-emerald-400' : t === 'neg' ? 'text-rose-400' : 'text-text-primary';

  return (
    <section className="rounded-2xl border border-bg-border bg-bg-secondary">
      <div className="flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-2 px-4 sm:px-5 py-3">
        <button type="button" onClick={() => setOpen((o) => !o)}
          title={open ? 'Collapse' : 'Expand'}
          className="p-1 rounded-lg text-text-muted hover:text-text-primary hover:bg-bg-panel transition-colors shrink-0">
          <ChevronDown className={`w-4 h-4 transition-transform ${open ? '' : '-rotate-90'}`} />
        </button>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <User className="w-3.5 h-3.5 text-text-muted shrink-0" />
            <span className="text-[13px] font-bold text-text-primary truncate">
              {email || 'Unassigned'}
            </span>
            <span className="shrink-0 inline-flex items-center rounded border border-bg-border bg-bg-primary/60 px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-text-muted">
              Holder
            </span>
            {status}
          </div>
          <div className="text-[11px] text-text-muted mt-0.5 truncate">{subtitle}</div>
        </div>

        <div className="basis-full sm:basis-auto shrink-0 flex items-center justify-end gap-3 sm:gap-6 pl-8 sm:pl-0">
          {stats.map((s) => (
            <div key={s.label} className="w-[4.75rem] sm:w-[6.75rem] shrink-0 text-right">
              <div className="text-[8px] uppercase tracking-wider text-text-muted/70 leading-none mb-0.5 whitespace-nowrap">
                {s.label}
              </div>
              <div className={`text-[13px] font-mono font-bold ${tone(s.tone)}`}>{s.value}</div>
            </div>
          ))}
        </div>
      </div>

      {open && <div className="px-2 sm:px-3 pb-3 space-y-4">{children}</div>}
    </section>
  );
}
