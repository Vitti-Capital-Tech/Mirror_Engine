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
  const errors = accounts.filter(a => a.status === 'error' || a.status === 'circuit_break').length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-bg-border/50 pb-4 select-none">
        <div>
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Configure API Credentials</h2>
        </div>
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
          { label: 'Total Accounts', value: total, border: 'border-bg-border' },
          { label: 'Active Sessions', value: active, border: 'border-bg-border', color: 'text-emerald-400' },
          { label: 'Paused Copies', value: paused, border: 'border-bg-border', color: 'text-amber-400' },
          { label: 'Circuit Broken', value: errors, border: 'border-bg-border', color: errors > 0 ? 'text-red-400' : 'text-text-muted' },
        ].map((item, i) => (
          <div key={i} className={`bg-bg-panel border ${item.border} rounded-lg px-4 py-3 flex flex-col justify-between`}>
            <span className="text-[10px] text-text-secondary uppercase font-bold tracking-wider">{item.label}</span>
            <span className={`text-lg font-bold mt-1 ${item.color || 'text-text-primary'}`}>{isLoading ? '-' : item.value}</span>
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
