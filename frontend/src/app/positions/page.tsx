'use client';
import React, { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions } from '@/hooks/usePositions';
import { useAccounts } from '@/hooks/useAccounts';
import { useAccountScope, applyScope, groupByMaster } from '@/context/AccountScopeContext';
import { useAuth } from '@/context/AuthContext';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/lib/api';
import { RefreshCw } from 'lucide-react';
import { Loader } from '@/components/shared/Loader';
import { PositionGroup, PCOL } from '@/components/positions/PositionGroup';

/**
 * Open positions, by group.
 *
 * This used to render two flat lists for a normal user — "Master Account" and
 * "Follower Accounts (n)" — and a different, grouped layout for an admin. Two
 * layouts meant the view most people saw was the one that showed least: with
 * several masters under one login, a flat follower list says nothing about
 * which copy belongs to which master.
 *
 * Everyone now gets the same grouped view. The admin difference is one line of
 * extra information — whose accounts these are — not a different page.
 */
export default function PositionsPage() {
  const queryClient = useQueryClient();
  const { data: accounts = [], isLoading: accountsLoading } = useAccounts();
  const { data: positions = [], isLoading: positionsLoading } = usePositions();
  const { scope } = useAccountScope();
  const { user } = useAuth();
  const { latestPosition } = useSocket();
  const [syncing, setSyncing] = useState(false);

  // Refresh on a WS position event.
  useEffect(() => {
    if (latestPosition) {
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
    }
  }, [latestPosition, queryClient]);

  const handleSyncLive = React.useCallback(async () => {
    try {
      setSyncing(true);
      await api.positions.syncLive();
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
      queryClient.invalidateQueries({ queryKey: ['account-live-view'] });
    } catch (e) {
      console.error('Failed to sync live positions:', e);
    } finally {
      setSyncing(false);
    }
  }, [queryClient]);

  useEffect(() => { handleSyncLive(); }, [handleSyncLive]);

  // Paused accounts are intentionally hidden here; the header switcher then
  // narrows to one owner when one has been picked.
  const visible = applyScope(
    (accounts as any[]).filter((a: any) => a.status !== 'paused'), scope);
  const groups = groupByMaster(visible as any[]);
  const isAdmin = user?.role === 'admin';

  const scoped = (positions as any[]).filter((p: any) =>
    visible.some((a: any) => String(a.id) === String(p.account_id)));
  const totalUnreal = scoped.reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);
  // Today's P&L is the account's own figure, not something derived from the
  // open legs — it includes trades already closed today, which is exactly the
  // part unrealised cannot see.
  const totalToday = (visible as any[]).reduce((s: number, a: any) => s + Number(a.today_pnl || 0), 0);
  const totalBalance = (visible as any[]).reduce((s: number, a: any) => s + Number(a.balance || 0), 0);
  const pnlClass = (v: number) =>
    v > 0 ? 'text-emerald-400' : v < 0 ? 'text-rose-400' : 'text-text-muted';
  const signed = (v: number) => `${v > 0 ? '+' : ''}${money(v)}`;
  const money = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const loading = (accountsLoading || positionsLoading) && accounts.length === 0;

  return (
    <div className="space-y-5 select-none">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        {/* Wraps: four figures do not fit across a phone, and the last of
            them was being cut off at the edge. */}
        <div className="flex items-end flex-wrap gap-x-6 gap-y-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Open positions</div>
            <div className="text-[15px] font-bold text-text-primary leading-tight">
              {scoped.length}
              <span className="text-[12px] font-normal text-text-muted">
                {' '}across {groups.length} {groups.length === 1 ? 'group' : 'groups'}
              </span>
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Balance</div>
            <div className="text-[15px] font-mono font-bold text-text-primary leading-tight">
              {money(totalBalance)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Today</div>
            <div className={`text-[15px] font-mono font-bold leading-tight ${pnlClass(totalToday)}`}>
              {signed(totalToday)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Unrealised</div>
            <div className={`text-[15px] font-mono font-bold leading-tight ${pnlClass(totalUnreal)}`}>
              {scoped.length ? signed(totalUnreal) : '—'}
            </div>
          </div>
        </div>

        <button onClick={handleSyncLive} disabled={syncing}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-60 disabled:cursor-not-allowed text-white text-xs font-semibold transition-colors">
          <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? 'Syncing…' : 'Sync now'}
        </button>
      </div>

      {loading ? <Loader label="Loading positions…" /> : groups.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-bg-border py-14 text-center">
          <p className="text-[13px] font-semibold text-text-primary">Nothing to show</p>
          <p className="text-[12px] text-text-muted mt-1">
            No active accounts. Paused accounts are not listed here.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Column labels, once, on the widths the rows use. On a phone the
              rows carry their own small labels instead — there is no room for
              a header that would sit over nothing. */}
          <div className="hidden sm:flex items-center gap-6 pr-5 text-[10px] uppercase tracking-wider text-text-muted/70">
            <div className="flex-1" />
            <div className={PCOL.balance}>Balance</div>
            <div className={PCOL.today}>Today</div>
            <div className={PCOL.unreal}>Unrealised</div>
          </div>
          {groups.map((g) => (
            <PositionGroup key={g.groupId} group={g} positions={positions as any[]}
              showOwner={isAdmin} />
          ))}
        </div>
      )}
    </div>
  );
}
