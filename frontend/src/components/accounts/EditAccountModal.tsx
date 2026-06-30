'use client';
import React, { useState } from 'react';
import { useUpdateAccount } from '@/hooks/useAccounts';
import { Select } from '@/components/shared/Select';
import { X, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';

export function EditAccountModal({ account, onClose }: { account: any; onClose: () => void }) {
  const updateAccount = useUpdateAccount();

  const [name, setName] = useState(account.name || '');
  const [allocatedBalance, setAllocatedBalance] = useState<string>(
    account.allocated_balance != null ? String(account.allocated_balance) : ''
  );
  const [allocationMode, setAllocationMode] = useState<string>(account.allocation_mode || 'auto_ratio');
  const [allocationValue, setAllocationValue] = useState<string>(
    account.allocation_value != null ? String(account.allocation_value) : '1.0'
  );
  const [maxPositionSize, setMaxPositionSize] = useState<string>(
    account.max_position_size != null ? String(account.max_position_size) : ''
  );
  const [saving, setSaving] = useState(false);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    const data: any = {
      name,
      // send null to clear, or the parsed number
      allocated_balance: allocatedBalance ? parseFloat(allocatedBalance) : null,
    };
    if (!account.is_master) {
      data.allocation_mode = allocationMode;
      data.allocation_value = allocationValue ? parseFloat(allocationValue) : 1.0;
      data.max_position_size = maxPositionSize ? parseFloat(maxPositionSize) : null;
    }
    try {
      await updateAccount.mutateAsync({ id: account.id, data });
      toast.success(`"${name}" updated.`);
      onClose();
    } catch (err: any) {
      toast.error(err.message || 'Failed to update account.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-primary/80 backdrop-blur-sm p-4 select-none">
      <div className="card-premium w-full max-w-[460px] overflow-hidden shadow-2xl animate-slide-in">
        <div className="flex items-center justify-between px-6 py-4 border-b border-bg-border">
          <h3 className="text-sm font-bold text-text-primary tracking-wide">EDIT ACCOUNT — {account.name}</h3>
          <button onClick={onClose} className="p-1 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-white transition-all">
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSave} className="p-6 space-y-4 text-xs font-semibold text-text-secondary">
          <div className="flex flex-col gap-1.5">
            <label className="text-text-muted uppercase tracking-wider text-[10px]">Account Name</label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-bg-primary border border-bg-border rounded-lg px-3 py-2 text-text-primary focus:border-blue-500 outline-none transition-all"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-text-muted uppercase tracking-wider text-[10px]">Allocated Balance (USD) — optional</label>
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder="Leave blank to use real balance"
              value={allocatedBalance}
              onChange={(e) => setAllocatedBalance(e.target.value)}
              className="bg-bg-primary border border-bg-border rounded-lg px-3 py-2 text-text-primary focus:border-blue-500 outline-none transition-all font-mono"
            />
            <p className="text-[10px] text-text-muted font-normal leading-relaxed">
              Overrides the real balance for the copy ratio. {account.is_master ? 'This is the ratio denominator.' : 'This is the ratio numerator.'}
            </p>
          </div>

          {!account.is_master && (
            <div className="border border-bg-border rounded-xl p-4 bg-bg-primary/45 space-y-3">
              <div className="text-[10px] text-blue-400 font-bold uppercase tracking-wider">Follower Copy Settings</div>
              <div className="flex flex-col gap-1.5">
                <label className="text-text-muted uppercase tracking-wider text-[9px]">Copy Mode</label>
                <Select
                  value={allocationMode}
                  onChange={setAllocationMode}
                  options={[
                    { value: 'auto_ratio', label: 'Auto Balance Ratio (Recommended)' },
                    { value: 'multiplier', label: 'Multiplier (x Size)' },
                  ]}
                />
              </div>
              {allocationMode !== 'auto_ratio' && (
                <div className="flex flex-col gap-1.5">
                  <label className="text-text-muted uppercase tracking-wider text-[9px]">Multiplier Value</label>
                  <input
                    type="number" step="0.01" min="0.01"
                    value={allocationValue}
                    onChange={(e) => setAllocationValue(e.target.value)}
                    className="bg-bg-primary border border-bg-border rounded-lg px-3 py-2 text-text-primary focus:border-blue-500 outline-none"
                  />
                </div>
              )}
              <div className="flex flex-col gap-1.5">
                <label className="text-text-muted uppercase tracking-wider text-[9px]">Max Position Size (lots) — optional</label>
                <input
                  type="number" step="1" min="1"
                  placeholder="No cap"
                  value={maxPositionSize}
                  onChange={(e) => setMaxPositionSize(e.target.value)}
                  className="bg-bg-primary border border-bg-border rounded-lg px-3 py-2 text-text-primary focus:border-blue-500 outline-none font-mono"
                />
              </div>
            </div>
          )}

          <div className="flex gap-3 pt-2">
            <button type="button" onClick={onClose} disabled={saving}
              className="flex-1 py-2 bg-bg-secondary hover:bg-bg-border/60 text-text-primary rounded-lg border border-bg-border transition-all font-semibold">
              Cancel
            </button>
            <button type="submit" disabled={saving}
              className="flex-1 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-all flex items-center justify-center gap-1.5 font-semibold">
              {saving ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" /> Saving…</> : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
