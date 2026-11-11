'use client';
import React, { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { usePositions } from '@/hooks/usePositions';
import { useAccounts } from '@/hooks/useAccounts';
import { useAccountScope, applyScope, groupByOwner, effectiveScope, ALL_ACCOUNTS } from '@/context/AccountScopeContext';
import { useAuth } from '@/context/AuthContext';
import { OwnerGroup } from '@/components/shared/OwnerGroup';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/lib/api';
import { RefreshCw } from 'lucide-react';
import { Loader } from '@/components/shared/Loader';
import { AccountPositionCard } from '@/components/positions/AccountPositionCard';

export default function PositionsPage() {
  const queryClient = useQueryClient();
  const { data: accounts = [], isLoading: accountsLoading } = useAccounts();
  const { scope } = useAccountScope();
  const { user } = useAuth();
  const { isLoading: positionsLoading } = usePositions();
  const { latestPosition } = useSocket();
  const [syncing, setSyncing] = useState(false);

  // Refresh React Query data on WS position event
  useEffect(() => {
    if (latestPosition) {
      queryClient.invalidateQueries({ queryKey: ['positions'] });
      queryClient.invalidateQueries({ queryKey: ['accounts'] });
    }
  }, [latestPosition, queryClient]);

  // Automatically sync positions from live exchange on initial page mount
  useEffect(() => {
    handleSyncLive();
  }, []);

  const handleSyncLive = async () => {
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
  };

  return (
    <div className="space-y-8 select-none">
      {/* Header action button */}
      <div className="flex items-center justify-end">
        <button
          onClick={handleSyncLive}
          disabled={syncing}
          className="group relative inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold text-white overflow-hidden border border-blue-400/30 bg-gradient-to-b from-blue-500 to-blue-600 hover:from-blue-400 hover:to-blue-500 shadow-lg shadow-blue-500/25 transition-all disabled:opacity-70 disabled:cursor-not-allowed"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : 'group-hover:rotate-90 transition-transform duration-300'}`} />
          {syncing ? 'Syncing…' : 'Sync Now'}
        </button>
      </div>

      {/* Grouped account cards: master on top, active followers below.
          Paused accounts are intentionally hidden from the positions view. */}
      {(accountsLoading || positionsLoading) && accounts.length === 0 ? <Loader label="Loading positions…" /> : (() => {
        // Paused accounts are intentionally hidden here; the TopBar switcher
        // then narrows to one account when the user has picked one.
        const visible = applyScope(
          accounts.filter((a: any) => a.status !== 'paused'), scope);
        const masterAccts = visible.filter((a: any) => a.is_master);
        const followerAccts = visible.filter((a: any) => !a.is_master);

        const renderCard = (acc: any) => <AccountPositionCard key={acc.id} acc={acc} />;

        // An admin looking at everything needs to see WHOSE accounts these are.
        // Flat "Master / Followers" headings read as one desk, which is exactly
        // the confusion: it looks like you are signed in as a single user. So
        // group by owner, each card headed by the person, master and followers
        // together inside. Done even for a single owner — naming them is the
        // point, not just separating them.
        const isAdmin = user?.role === 'admin';
        if (isAdmin && effectiveScope(visible as any[], scope) === ALL_ACCOUNTS && visible.length > 0) {
          const groups = groupByOwner(visible as any[]);
          return (
            <div className="space-y-5">
              {groups.map((g) => (
                <OwnerGroup
                  key={g.ownerId}
                  email={(g.master as any)?.owner_email
                    ?? (g.followers[0] as any)?.owner_email}
                  master={g.master}
                  followers={g.followers}
                >
                  {g.master && renderCard(g.master)}
                  {g.followers.map(renderCard)}
                  {!g.master && (
                    <div className="card-premium py-5 text-center text-text-muted text-xs">
                      No active master account for this owner.
                    </div>
                  )}
                </OwnerGroup>
              ))}
            </div>
          );
        }

        return (
          <div className="space-y-10">
            {/* Master section */}
            <div className="space-y-3">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.15em]">Master Account</h3>
              {masterAccts.length > 0 ? (
                <div className="space-y-6">{masterAccts.map(renderCard)}</div>
              ) : (
                <div className="card-premium py-6 text-center text-text-muted text-xs">
                  No active master account.
                </div>
              )}
            </div>

            {/* Followers section */}
            <div className="space-y-3">
              <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-[0.15em]">
                Follower Accounts ({followerAccts.length})
              </h3>
              {followerAccts.length > 0 ? (
                <div className="space-y-6">{followerAccts.map(renderCard)}</div>
              ) : (
                <div className="card-premium py-6 text-center text-text-muted text-xs">
                  No active follower accounts.
                </div>
              )}
            </div>
          </div>
        );
      })()}
    </div>
  );
}
