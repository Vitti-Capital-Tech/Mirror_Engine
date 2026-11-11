'use client';
import React, { useState } from 'react';
import { useAccounts } from '@/hooks/useAccounts';
import { useAccountScope, applyScope, groupByOwner, effectiveScope, ALL_ACCOUNTS } from '@/context/AccountScopeContext';
import { useAuth } from '@/context/AuthContext';
import { OwnerGroup } from '@/components/shared/OwnerGroup';
import { AccountsTable } from '@/components/accounts/AccountsTable';
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
  const active = accounts.filter(a => a.status === 'active').length;
  const paused = accounts.filter(a => a.status === 'paused').length;
  const followers = accounts.filter(a => !a.is_master).length;

  return (
    <div className="space-y-6">
      {/* Header Action Row */}
      <div className="flex items-center justify-end pb-2 select-none">
        <button
          onClick={() => setIsAddModalOpen(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white text-xs font-semibold rounded-lg transition-all duration-200 shadow-md hover:shadow-blue-500/10"
        >
          <Plus className="w-3.5 h-3.5" />
          Add Account
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 select-none">
        {[
          { label: 'Total Accounts', value: total, color: 'text-text-primary' },
          { label: 'Active Sessions', value: active, color: 'text-emerald-400' },
          { label: 'Paused', value: paused, color: 'text-text-secondary' },
          { label: 'Followers', value: followers, color: 'text-blue-400' },
        ].map((item, i) => (
          <div key={i} className="card-premium px-4 py-3.5 flex flex-col gap-1">
            <span className="text-[10px] text-text-muted uppercase font-semibold tracking-[0.12em]">{item.label}</span>
            <span className={`text-xl font-bold font-mono ${item.color}`}>{isLoading ? '–' : item.value}</span>
          </div>
        ))}
      </div>

      {/* Accounts table — one per owner when an admin is looking at everything.
          A single flat table reads as one person's desk, which is exactly wrong
          here: these accounts belong to different people and the controls on
          them are destructive. Pausing or deleting the wrong account because
          two tenants' rows sat under one "FOLLOWER ACCOUNTS" heading is a real
          mistake to design out. */}
      {isLoading ? <Loader label="Loading accounts…" /> : (
        user?.role === 'admin' && effectiveScope(accounts, scope) === ALL_ACCOUNTS && accounts.length > 0 ? (
          <div className="space-y-5">
            {groupByOwner(accounts).map((g) => (
              <OwnerGroup
                key={g.ownerId}
                email={(g.master as any)?.owner_email ?? (g.followers[0] as any)?.owner_email}
                master={g.master}
                followers={g.followers}
              >
                <AccountsTable
                  accounts={[...(g.master ? [g.master] : []), ...g.followers]}
                  isLoading={false}
                />
              </OwnerGroup>
            ))}
          </div>
        ) : <AccountsTable accounts={accounts} isLoading={false} />
      )}

      {/* Add Account Modal */}
      {isAddModalOpen && (
        <AddAccountModal isOpen={isAddModalOpen} onClose={() => setIsAddModalOpen(false)} />
      )}
    </div>
  );
}
