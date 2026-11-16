'use client';
import React, { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions } from '@/hooks/usePositions';
import { useAccounts } from '@/hooks/useAccounts';
import { useAccountScope, applyScope, groupByMaster, groupByOwner, effectiveScope, ALL_ACCOUNTS } from '@/context/AccountScopeContext';
import { useAuth } from '@/context/AuthContext';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/lib/api';
import { RefreshCw } from 'lucide-react';
import { Loader } from '@/components/shared/Loader';
import { PositionGroup } from '@/components/positions/PositionGroup';
import { OwnerSection } from '@/components/shared/OwnerSection';
import { ViewingAs } from '@/components/shared/ViewingAs';
import { DeskHealth, countMismatches } from '@/components/shared/DeskHealth';

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
  const { scope, setScope } = useAccountScope();
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
  // Whose desk is on screen. A holder scope names the person directly; a group
  // scope names a master, so the person is read off that group's accounts.
  const viewingLabel = (() => {
    const inScope = visible as any[];
    return inScope[0]?.owner_email || inScope[0]?.name || 'this holder';
  })();

  const scoped = (positions as any[]).filter((p: any) =>
    visible.some((a: any) => String(a.id) === String(p.account_id)));
  const totalUnreal = scoped.reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);
  // Today's P&L is the account's own figure, not something derived from the
  // open legs — it includes trades already closed today, which is exactly the
  // part unrealised cannot see.
  // Summed from the two figures beside it, not read from today_pnl — see
  // PositionGroup: the stored value folds in an older unrealised and would
  // leave the header visibly not adding up.
  const totalBalance = (visible as any[]).reduce((s: number, a: any) => s + Number(a.balance || 0), 0);
  const totalRealised = (visible as any[]).reduce((s: number, a: any) => s + Number(a.realized_pnl_today || 0), 0);
  const totalToday = totalRealised + totalUnreal;
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
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Realised today</div>
            <div className={`text-[15px] font-mono font-bold leading-tight ${pnlClass(totalRealised)}`}>
              {signed(totalRealised)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Unrealised</div>
            <div className={`text-[15px] font-mono font-bold leading-tight ${pnlClass(totalUnreal)}`}>
              {scoped.length ? signed(totalUnreal) : '—'}
            </div>
          </div>
          <div>
            {/* The whole, after its two halves — realised + unrealised. */}
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Today</div>
            <div className={`text-[15px] font-mono font-bold leading-tight ${pnlClass(totalToday)}`}>
              {signed(totalToday)}
            </div>
          </div>
        </div>

        <button onClick={handleSyncLive} disabled={syncing}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-60 disabled:cursor-not-allowed text-white text-xs font-semibold transition-colors">
          <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
          {syncing ? 'Syncing…' : 'Sync now'}
        </button>
      </div>

      {/* Scoped to one person, this page is identical to that person's own —
          which is the point, and the reason it has to say whose it is before
          anyone adds an account on it. */}
      {isAdmin && effectiveScope(visible as any[], scope) !== ALL_ACCOUNTS && (
        <ViewingAs
          label={viewingLabel}
          detail="this is exactly what they see"
          status={<DeskHealth
            mismatches={countMismatches(groups as any[], positions as any[])}
            failing={(visible as any[]).filter((a: any) => Number(a.consecutive_failures || 0) > 0).length}
          />}
          onClear={() => setScope(ALL_ACCOUNTS)}
        />
      )}

      {loading ? <Loader label="Loading positions…" /> : groups.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-bg-border py-14 text-center">
          <p className="text-[13px] font-semibold text-text-primary">Nothing to show</p>
          <p className="text-[12px] text-text-muted mt-1">
            No active accounts. Paused accounts are not listed here.
          </p>
        </div>
      ) : (
        <div className="space-y-5">
          {/* Same rule as the accounts list: holders only when an admin is
              looking at everyone at once. */}
          {isAdmin && effectiveScope(visible as any[], scope) === ALL_ACCOUNTS
            ? groupByOwner(groups).map((o) => {
                const accs = o.groups.flatMap((g) => [...(g.master ? [g.master] : []), ...g.followers]) as any[];
                const ids = new Set(accs.map((a: any) => String(a.id)));
                const legs = (positions as any[]).filter((p) => ids.has(String(p.account_id)));
                const unreal = legs.reduce((s: number, p: any) => s + Number(p.unrealized_pnl || 0), 0);
                const bal = accs.reduce((s: number, a: any) => s + Number(a.balance || 0), 0);

                const real = accs.reduce((s: number, a: any) => s + Number(a.realized_pnl_today || 0), 0);
                const day = real + unreal;
                return (
                  <OwnerSection key={o.ownerId} email={o.email}
                    subtitle={`${o.groups.length} group${o.groups.length === 1 ? '' : 's'} · ${accs.length} account${accs.length === 1 ? '' : 's'} · ${legs.length} open`}
                    status={<DeskHealth
                      mismatches={countMismatches(o.groups as any[], positions as any[])}
                      failing={accs.filter((a: any) => Number(a.consecutive_failures || 0) > 0).length}
                    />}
                    stats={[
                      { label: 'Total balance', value: money(bal) },
                      { label: 'Total realised', value: signed(real),
                        tone: real > 0 ? 'pos' : real < 0 ? 'neg' : 'flat' },
                      { label: 'Total unreal.', value: legs.length ? signed(unreal) : '—',
                        tone: unreal > 0 ? 'pos' : unreal < 0 ? 'neg' : 'flat' },
                      { label: 'Total today', value: signed(day), tone: day > 0 ? 'pos' : day < 0 ? 'neg' : 'flat' },
                    ]}>
                    {o.groups.map((g) => (
                      <PositionGroup key={g.groupId} group={g} positions={positions as any[]}
                        showOwner={false} />
                    ))}
                  </OwnerSection>
                );
              })
            : groups.map((g) => (
                <PositionGroup key={g.groupId} group={g} positions={positions as any[]}
                  showOwner={false} />
              ))}
        </div>
      )}
    </div>
  );
}
