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

/** The group an account belongs to: its master's id.
 *
 *  A master is the head of its own group; a follower belongs to the master it
 *  mirrors. One login may run several independent groups, so the owner is no
 *  longer the unit — two of an owner's masters are two separate things and must
 *  never be shown, or scoped, as one.
 *
 *  A follower with no master yet falls back to an owner-level key so it is
 *  still visible somewhere rather than silently dropped. */
export function groupKeyOf(a: { id?: string; is_master?: boolean; master_id?: string | null; owner_id?: string | null }): string {
  if (a.is_master) return String(a.id);
  if (a.master_id) return String(a.master_id);
  return `owner:${a.owner_id ?? 'unassigned'}`;
}

/** Narrow a list of accounts to the selected group. */
export function applyScope<T extends { id?: string; is_master?: boolean; master_id?: string | null; owner_id?: string | null }>(
  accounts: T[], scope: string,
): T[] {
  if (!scope || scope === ALL_ACCOUNTS) return accounts;
  const hit = accounts.filter((a) => groupKeyOf(a) === scope);
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
export function effectiveScope<T extends { id?: string; is_master?: boolean; master_id?: string | null; owner_id?: string | null }>(
  accounts: T[], scope: string,
): string {
  if (!scope || scope === ALL_ACCOUNTS) return ALL_ACCOUNTS;
  return accounts.some((a) => groupKeyOf(a) === scope) ? scope : ALL_ACCOUNTS;
}

/** One group: a master and the followers assigned to it. */
export interface AccountGroup<T> {
  groupId: string;          // the master's id (or an owner key for unassigned)
  ownerId?: string | null;  // who the group belongs to, for the heading
  master?: T;
  followers: T[];
  label: string;
}

/**
 * Group accounts by MASTER, not by owner.
 *
 * One login may run several independent master/follower groups, so an owner is
 * no longer one thing to show. Grouping by owner would stack two unrelated
 * masters under a single heading with nothing saying they are separate desks —
 * and on /accounts, where the rows carry pause and delete, that is how the
 * wrong account gets acted on.
 *
 * Followers with no master yet are collected under an owner-level key so they
 * remain visible and obviously unassigned, rather than disappearing.
 */
export function groupByMaster<T extends { id: string; name?: string; is_master?: boolean; master_id?: string | null; owner_id?: string | null }>(
  accounts: T[],
): AccountGroup<T>[] {
  // Plain object + explicit ordering rather than spreading a Map: the tsconfig
  // here targets below es2015, where spreading a Map iterator does not compile.
  const byGroup: Record<string, T[]> = {};
  const order: string[] = [];
  // Masters first, so a group is keyed and ordered by its master even when a
  // follower happens to come earlier in the list.
  const sorted = [...accounts].sort((a, b) => Number(!!b.is_master) - Number(!!a.is_master));
  for (const a of sorted) {
    const key = groupKeyOf(a);
    if (!byGroup[key]) { byGroup[key] = []; order.push(key); }
    byGroup[key].push(a);
  }
  return order.map((groupId) => {
    const list: T[] = byGroup[groupId];
    const master = list.find((a: T) => a.is_master);
    const followers = list.filter((a: T) => !a.is_master);
    // Named after the master — that is what anyone recognises. ids are uuids.
    const label = master?.name || followers[0]?.name || 'Unassigned';
    return { groupId, ownerId: (master ?? followers[0])?.owner_id, master, followers, label };
  });
}
