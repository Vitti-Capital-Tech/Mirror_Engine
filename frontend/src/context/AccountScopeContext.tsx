'use client';
import React, { createContext, useContext, useEffect, useState } from 'react';

/**
 * Which SET of accounts the app is focused on, or all of them.
 *
 * The backend already decides what you may see — scope_owned() filters by
 * owner_id and an admin is exempt, so signing in with `trade` returns every
 * account in the system. What was missing was a way to look at one at a time:
 * /positions stacked everything it was given, which is fine for one master and
 * one follower and unreadable once several people's accounts are in the list.
 *
 * The unit here is the OWNER, not the account. A master on its own, or a
 * follower on its own, is not a thing anyone wants to look at in a copy-trading
 * dashboard — the whole question is "is this follower matching its master?",
 * which needs both side by side. So the scope is an owner_id and selecting it
 * brings the master and its followers together.
 *
 * Deliberately a VIEW filter and nothing more. It never changes what is asked
 * of the API and never reaches the copy engine — picking a group here cannot
 * start, stop or re-scope any trading.
 */

export const ALL_ACCOUNTS = 'all';
const KEY = 'me_account_scope';

interface AccountScope {
  scope: string;                 // an owner_id, or ALL_ACCOUNTS
  setScope: (ownerId: string) => void;
}

const Ctx = createContext<AccountScope | undefined>(undefined);

export function AccountScopeProvider({ children }: { children: React.ReactNode }) {
  // Read on mount rather than in the initialiser: this renders on the server
  // too, and localStorage would make the first client render disagree with it.
  const [scope, setScopeState] = useState<string>(ALL_ACCOUNTS);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved) setScopeState(saved);
    } catch { /* private window, blocked storage — the default is fine */ }
  }, []);

  const setScope = (id: string) => {
    setScopeState(id);
    try { localStorage.setItem(KEY, id); } catch { /* not worth failing over */ }
  };

  return <Ctx.Provider value={{ scope, setScope }}>{children}</Ctx.Provider>;
}

export function useAccountScope(): AccountScope {
  const ctx = useContext(Ctx);
  // No provider (an auth page, say) — behave as "showing everything" rather
  // than throwing, so a page can use this without caring where it is mounted.
  return ctx ?? { scope: ALL_ACCOUNTS, setScope: () => {} };
}

/** Narrow a list of accounts to the selected owner's group. */
export function applyScope<T extends { owner_id?: string | null }>(
  accounts: T[], scope: string,
): T[] {
  if (!scope || scope === ALL_ACCOUNTS) return accounts;
  const hit = accounts.filter((a) => a.owner_id === scope);
  // A scope pointing at a group that no longer exists — its accounts deleted,
  // or transferred to someone else — must not render an empty dashboard with
  // no explanation. Fall back to showing everything.
  return hit.length ? hit : accounts;
}

/**
 * The scope actually in force.
 *
 * A stored scope can stop matching anything — the group was transferred away,
 * its accounts were deleted, or (as happened here) the stored value predates a
 * change in what a scope IS and is now an account id where an owner_id is
 * expected. Everything must agree about that: applyScope already falls back to
 * showing all accounts and the switcher already falls back to the "All
 * accounts" label, so a page deciding whether to group had to reach the same
 * answer or the header would read "All accounts" while the view quietly stayed
 * ungrouped.
 */
export function effectiveScope<T extends { owner_id?: string | null }>(
  accounts: T[], scope: string,
): string {
  if (!scope || scope === ALL_ACCOUNTS) return ALL_ACCOUNTS;
  return accounts.some((a) => a.owner_id === scope) ? scope : ALL_ACCOUNTS;
}

/** Group accounts by owner: one entry per set of master + its followers. */
export interface AccountGroup<T> {
  ownerId: string;
  master?: T;
  followers: T[];
  label: string;
}

export function groupByOwner<T extends { id: string; name?: string; is_master?: boolean; owner_id?: string | null }>(
  accounts: T[],
): AccountGroup<T>[] {
  // Plain object + Array.from rather than spreading a Map: the tsconfig here
  // targets below es2015, where spreading a Map iterator does not compile.
  const byOwner: Record<string, T[]> = {};
  const order: string[] = [];
  for (const a of accounts) {
    const key = String(a.owner_id ?? 'unassigned');
    if (!byOwner[key]) { byOwner[key] = []; order.push(key); }
    byOwner[key].push(a);
  }
  return order.map((ownerId) => {
    const list: T[] = byOwner[ownerId];
    const master = list.find((a: T) => a.is_master);
    const followers = list.filter((a: T) => !a.is_master);
    // Named after the master, because that is what anyone recognises — the
    // owner_id is a uuid and the accounts API does not return the email.
    const label = master?.name || followers[0]?.name || 'Unassigned';
    return { ownerId, master, followers, label };
  });
}
