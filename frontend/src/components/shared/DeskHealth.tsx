'use client';
import React from 'react';
import { AlertTriangle, Check } from 'lucide-react';

/**
 * Whether anything on this desk needs a human.
 *
 * A holder band that reports only balance and P&L answers "how much" and
 * never "is it working" — which is the question an admin opened the page to
 * ask. Money looks identical whether every follower is mirroring correctly or
 * one of them stopped an hour ago; the figures would carry on moving either
 * way, because the master's own position is still making them move.
 *
 * Three things can be wrong and none of them show up in a total:
 *   - a follower is missing a leg its master holds (it is not mirroring),
 *   - an account is paused (someone stopped it, possibly and forgot),
 *   - an account's key is failing (it will not be able to trade).
 *
 * Saying "nothing needs attention" out loud matters as much as the warning:
 * an absent badge is indistinguishable from a badge that failed to render.
 */
export function DeskHealth({ mismatches = 0, paused = 0, failing = 0 }: {
  mismatches?: number;
  paused?: number;
  failing?: number;
}) {
  const parts: string[] = [];
  if (mismatches) parts.push(`${mismatches} not mirroring`);
  if (failing) parts.push(`${failing} key failing`);
  if (paused) parts.push(`${paused} paused`);

  if (!parts.length) {
    return (
      <span className="inline-flex items-center gap-1 rounded border border-emerald-500/25 bg-emerald-500/10 px-1.5 py-px text-[9.5px] font-semibold text-emerald-300/90">
        <Check className="w-2.5 h-2.5" /> Nothing needs attention
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-px text-[9.5px] font-semibold text-amber-300">
      <AlertTriangle className="w-2.5 h-2.5" /> {parts.join(' · ')}
    </span>
  );
}

/** Followers holding fewer symbols than their master. Counted per group, from
 *  the same positions list the rows read, so it cannot disagree with them. */
export function countMismatches(
  groups: { master?: any; followers: any[] }[],
  positions: any[],
): number {
  let n = 0;
  for (const g of groups) {
    if (!g.master) continue;
    const held = (id: string) =>
      new Set(positions.filter((p) => String(p.account_id) === String(id)).map((p) => p.symbol));
    const masterSyms = held(g.master.id);
    if (!masterSyms.size) continue;
    for (const f of g.followers) {
      const theirs = held(f.id);
      if (Array.from(masterSyms).some((s) => !theirs.has(s))) n += 1;
    }
  }
  return n;
}
