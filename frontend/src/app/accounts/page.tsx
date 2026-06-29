'use client';
import React, { useState } from 'react';
import { useAccounts } from '@/hooks/useAccounts';
import { AccountsTable } from '@/components/accounts/AccountsTable';
import { AddAccountModal } from '@/components/accounts/AddAccountModal';
import { Plus } from 'lucide-react';

export default function AccountsPage() {
  const { data: accounts = [], isLoading } = useAccounts();
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

      {/* Accounts Table */}
      <AccountsTable accounts={accounts} isLoading={isLoading} />

      {/* Add Account Modal */}
      {isAddModalOpen && (
        <AddAccountModal isOpen={isAddModalOpen} onClose={() => setIsAddModalOpen(false)} />
      )}
    </div>
  );
}
