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

/** A scope naming a PERSON rather than one of their groups.
 *
 *  Picking a holder is how an admin says "show me exactly what this person
 *  sees" — all of that person's groups, laid out the way they get them, with
 *  no holder bands and nothing asking which email anything belongs to. */
export const holderScope = (ownerId: string | null | undefined) =>
  `holder:${ownerId ?? 'unassigned'}`;

export const isHolderScope = (scope: string) => !!scope && scope.startsWith('holder:');

export const holderOf = (scope: string) =>
  isHolderScope(scope) ? scope.slice('holder:'.length) : null;

const matchesScope = (a: { owner_id?: string | null }, scope: string) =>
  isHolderScope(scope)
    ? String(a.owner_id ?? 'unassigned') === holderOf(scope)
    : groupKeyOf(a as any) === scope;

/** Narrow a list of accounts to the selected group, or to one holder. */
export function applyScope<T extends { id?: string; is_master?: boolean; master_id?: string | null; owner_id?: string | null }>(
  accounts: T[], scope: string,
): T[] {
  if (!scope || scope === ALL_ACCOUNTS) return accounts;
  const hit = accounts.filter((a) => matchesScope(a, scope));
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
  return accounts.some((a) => matchesScope(a, scope)) ? scope : ALL_ACCOUNTS;
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
/**
 * Which rung of the stop-loss ladder each follower sits on.
 *
 * The engine places follower 1's SL/TP one offset beyond its master's,
 * follower 2's two offsets beyond, and so on, so they exit in order behind him
 * instead of all on the same price. This is the UI's copy of that rule, and it
 * must match CopyEngine._tp_sl_params exactly -- a rung shown next to somebody's
 * live position is a statement about where their money comes off the table, so
 * a plausible-looking wrong number is worse than none.
 *
 * Two details are load-bearing:
 *
 *   * ACTIVE followers only. The engine skips a paused one, and everything
 *     behind it moves up. A paused follower therefore has NO rung, and callers
 *     should show it as a plain follower rather than invent a number for it.
 *   * ordered by created_at, NOT by the order the array happens to be in.
 *     groupByMaster preserves the API's response order and does not sort, so
 *     using the index would print the right numbers against the wrong names
 *     whenever the rows came back differently.
 *
 * Lives here, beside groupByMaster, because two pages need it -- the accounts
 * list and the positions page -- and two copies of a rule that has to agree
 * with the backend is one copy too many.
 */
export function followerRungs<T extends { id: string; status?: string; created_at?: string }>(
  followers: T[],
): Record<string, number> {
  const rungs: Record<string, number> = {};
  [...followers]
    .filter((f) => f.status === 'active')
    .sort((a, b) => String(a.created_at ?? '').localeCompare(String(b.created_at ?? '')))
    .forEach((f, i) => { rungs[String(f.id)] = i + 1; });
  return rungs;
}


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

  // A follower whose master_id is not set yet must still land WITH its master,
  // not in a group of its own. That happens for any row the backfill has not
  // reached, and for every row served by a backend that predates master_id —
  // during a rolling deploy, for instance. If its owner has exactly one master
  // there is no ambiguity, so adopt it; with several, leave it unassigned
  // rather than guessing which desk it belongs to.
  const mastersByOwner: Record<string, string[]> = {};
  for (const a of sorted) {
    if (!a.is_master) continue;
    const o = String(a.owner_id ?? '');
    (mastersByOwner[o] = mastersByOwner[o] || []).push(String(a.id));
  }
  const keyFor = (a: typeof sorted[number]) => {
    if (!a.is_master && !a.master_id) {
      const mine = mastersByOwner[String(a.owner_id ?? '')] || [];
      if (mine.length === 1) return mine[0];
    }
    return groupKeyOf(a);
  };

  for (const a of sorted) {
    const key = keyFor(a);
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


/**
 * Bucket master-groups by the person who holds them.
 *
 * Only an admin looking at EVERY account needs this. Grouping by master is
 * right for the person who owns the accounts — it is their desk and they know
 * whose it is. An admin looking at all of them sees a flat run of groups whose
 * only clue to ownership is a line of small print in each header, so two
 * holders' groups sit shoulder to shoulder and read as one book.
 *
 * This does not replace the master grouping; it wraps it. A holder contains
 * their groups, each group still contains its master and followers.
 */
export interface OwnerBucket<T> {
  ownerId: string;
  email?: string;
  groups: AccountGroup<T>[];
}

export function groupByOwner<T extends { owner_id?: string | null; owner_email?: string }>(
  groups: AccountGroup<T>[],
): OwnerBucket<T>[] {
  const order: string[] = [];
  const by: Record<string, OwnerBucket<T>> = {};
  for (const g of groups) {
    const key = String(g.ownerId ?? 'unassigned');
    if (!(key in by)) {
      const any = (g.master ?? g.followers[0]) as any;
      by[key] = { ownerId: key, email: any?.owner_email, groups: [] };
      order.push(key);
    }
    by[key].groups.push(g);
  }
  return order.map((k) => by[k]);
}
