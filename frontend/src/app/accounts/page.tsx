'use client';
import React, { useState } from 'react';
import { useAccounts } from '@/hooks/useAccounts';
import { useAccountScope, applyScope, groupByMaster, effectiveScope, ALL_ACCOUNTS } from '@/context/AccountScopeContext';
import { useAuth } from '@/context/AuthContext';
import { AccountGroupCard } from '@/components/accounts/AccountGroupCard';
import { AddAccountModal } from '@/components/accounts/AddAccountModal';
import { Loader } from '@/components/shared/Loader';
import { Plus } from 'lucide-react';

export default function AccountsPage() {
  const { data: allAccounts = [], isLoading } = useAccounts();
  // Admins get every account here (scope_owned exempts them), with the full
  // set of controls — add, edit, pause, resume, test, promote, delete — on all
  // of them regardless of who owns them. The header switcher narrows WHICH
  // owner's accounts are listed; it never removes a control.
  const { scope } = useAccountScope();
  const { user } = useAuth();
  const accounts = applyScope(allAccounts as any[], scope);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);

  const total = accounts.length;
  const paused = accounts.filter(a => a.status === 'paused').length;
  const totalBalance = accounts.reduce((s: number, a: any) => s + Number(a.balance || 0), 0);
  const todayPnl = accounts.reduce((s: number, a: any) => s + Number(a.today_pnl || 0), 0);
  const money2 = (v: number) =>
    v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  const groups = groupByMaster(accounts);
  const isAdmin = user?.role === 'admin';
  const MAX_MASTERS = 5, MAX_FOLLOWERS = 2;
  const masterCount = accounts.filter((a: any) => a.is_master).length;
  // The button now opens a form that can add either kind, so it may only be
  // disabled when there is nothing left to add at all — masters capped AND
  // every master's copy slots used. Disabling it on the master cap alone
  // (which is what it did when it only added masters) would lock someone out
  // of adding a copy the moment they had five masters.
  const everythingFull =
    masterCount >= MAX_MASTERS &&
    groups.every((g) => g.followers.length >= MAX_FOLLOWERS);
  const [presetMaster, setPresetMaster] = useState<string | undefined>(undefined);

  const openAdd = (masterId?: string) => { setPresetMaster(masterId); setIsAddModalOpen(true); };

  return (
    <div className="space-y-5">
      {/* One line instead of four stat cards. The cards took a quarter of the
          screen to say "2" and "0"; what someone actually needs to know here is
          how many groups they have and whether anything is paused. */}
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div className="flex items-end gap-7">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Accounts</div>
            <div className="text-[15px] font-bold text-text-primary leading-tight">
              {total}
              <span className="text-[12px] font-normal text-text-muted">
                {' '}in {groups.length} {groups.length === 1 ? 'group' : 'groups'}
                {paused > 0 && <span className="text-amber-400"> · {paused} paused</span>}
              </span>
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Total balance</div>
            <div className="text-[15px] font-mono font-bold text-text-primary leading-tight">
              {money2(totalBalance)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Today</div>
            <div className={`text-[15px] font-mono font-bold leading-tight ${
              todayPnl > 0 ? 'text-emerald-400' : todayPnl < 0 ? 'text-rose-400' : 'text-text-muted'}`}>
              {todayPnl > 0 ? '+' : ''}{money2(todayPnl)}
            </div>
          </div>
        </div>
        <button
          onClick={() => openAdd(undefined)}
          disabled={everythingFull}
          title={everythingFull
            ? `All ${MAX_MASTERS} masters are set up and each has its ${MAX_FOLLOWERS} copies`
            : undefined}
          className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-bg-panel disabled:text-text-muted disabled:cursor-not-allowed text-white text-xs font-semibold rounded-lg transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Add account
        </button>
      </div>

      {isLoading ? <Loader label="Loading accounts…" /> : groups.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-bg-border py-14 text-center">
          <p className="text-[13px] font-semibold text-text-primary">No accounts yet</p>
          <p className="text-[12px] text-text-muted mt-1 max-w-sm mx-auto">
            Start with a master — the account you trade yourself. You can then add
            up to {MAX_FOLLOWERS} copies that mirror it.
          </p>
          <button onClick={() => openAdd(undefined)}
            className="mt-4 inline-flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg transition-colors">
            <Plus className="w-3.5 h-3.5" /> Add account
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((g) => (
            <AccountGroupCard
              key={g.groupId}
              group={g}
              maxFollowers={MAX_FOLLOWERS}
              onAddCopy={(mid) => openAdd(mid)}
              showOwner={isAdmin}
            />
          ))}
        </div>
      )}

      {isAddModalOpen && (
        <AddAccountModal
          isOpen={isAddModalOpen}
          onClose={() => { setIsAddModalOpen(false); setPresetMaster(undefined); }}
          presetMasterId={presetMaster}
        />
      )}
    </div>
  );
}
