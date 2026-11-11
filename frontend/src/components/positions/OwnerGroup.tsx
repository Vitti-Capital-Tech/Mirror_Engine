'use client';
import React from 'react';
import { ChevronDown, Crown, CornerDownRight, User } from 'lucide-react';

/**
 * One owner's accounts, headed by the person they belong to.
 *
 * An admin is exempt from scope_owned(), so every tenant's accounts come back
 * in one list. Rendered flat as "MASTER ACCOUNT / FOLLOWER ACCOUNTS" that reads
 * as a single desk — you cannot tell whose accounts you are looking at, and
 * with two tenants you would get two masters stacked under one heading with
 * nothing saying they are different people.
 *
 * So on "All accounts" each owner gets their own card: the email on top, their
 * master and followers inside, collapsible so a long list stays navigable.
 * Collapsed state is per-owner and in component state only — it is a display
 * preference, not something worth persisting or syncing.
 */
export function OwnerGroup({
  email, master, followers, children, defaultOpen = true,
}: {
  email?: string | null;
  master?: any;
  followers: any[];
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const total = (master ? 1 : 0) + followers.length;

  return (
    <div className="rounded-2xl border border-bg-border bg-bg-panel/30 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 sm:px-5 py-3.5 hover:bg-bg-secondary/40 transition-colors text-left"
      >
        <span className="flex items-center justify-center w-8 h-8 rounded-xl bg-gradient-to-br from-blue-500/25 to-emerald-500/20 ring-1 ring-bg-border shrink-0">
          <User className="w-4 h-4 text-blue-300" />
        </span>

        <span className="min-w-0 flex-1">
          <span className="block text-[13px] font-bold text-text-primary truncate">
            {email || 'Unassigned'}
          </span>
          <span className="flex items-center gap-2.5 mt-0.5 text-[10.5px] text-text-muted">
            {master
              ? <span className="inline-flex items-center gap-1">
                  <Crown className="w-3 h-3 text-amber-400" />{master.name}
                </span>
              : <span>no master</span>}
            <span className="inline-flex items-center gap-1">
              <CornerDownRight className="w-3 h-3" />
              {followers.length} follower{followers.length === 1 ? '' : 's'}
            </span>
          </span>
        </span>

        <span className="text-[10px] font-bold text-text-muted border border-bg-border rounded px-1.5 py-0.5 shrink-0">
          {total} {total === 1 ? 'ACCOUNT' : 'ACCOUNTS'}
        </span>
        <ChevronDown className={`w-4 h-4 text-text-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && <div className="px-3 sm:px-4 pb-4 space-y-6">{children}</div>}
    </div>
  );
}
