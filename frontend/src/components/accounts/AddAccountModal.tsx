'use client';
import React, { useState } from 'react';
import { useCreateAccount, useAccounts } from '@/hooks/useAccounts';
import { X, Eye, EyeOff, ShieldCheck, RefreshCw } from 'lucide-react';
import { Select } from '@/components/shared/Select';

export function AddAccountModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const createAccount = useCreateAccount();
  const { data: accounts = [] } = useAccounts();
  const hasMaster = accounts.some((a: any) => a.is_master);

  // Form states
  const [name, setName] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [isMaster, setIsMaster] = useState(false);
  const [environment, setEnvironment] = useState<'demo' | 'live'>('demo');
  const [allocationMode, setAllocationMode] = useState<'fixed' | 'multiplier' | 'capital_pct' | 'auto_ratio'>('auto_ratio');
  const [allocationValue, setAllocationValue] = useState<string>('1.0');
  const [maxPositionSize, setMaxPositionSize] = useState<string>('');
  const [leverageLimit, setLeverageLimit] = useState<string>('');

  // UI States
  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    setErrorMsg('');

    try {
      const payload: any = {
        name,
        api_key: apiKey,
        api_secret: apiSecret,
        is_master: isMaster,
        environment,
      };

      if (leverageLimit) {
        payload.leverage_limit = parseInt(leverageLimit);
      }

      if (!isMaster) {
        payload.allocation_mode = allocationMode;
        payload.allocation_value = allocationValue ? parseFloat(allocationValue) : 1.0;
        if (maxPositionSize) {
          payload.max_position_size = parseFloat(maxPositionSize);
        }
      }

      await createAccount.mutateAsync(payload);
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to save account credentials.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg-primary/80 backdrop-blur-sm p-4 select-none">
      <div className="card-premium w-full max-w-[500px] overflow-hidden shadow-2xl animate-slide-in">
        
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bg-border">
          <h3 className="text-sm font-bold text-text-primary tracking-wide">ADD EXCHANGE ACCOUNT</h3>
          <button 
            onClick={onClose} 
            className="p-1 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-white transition-all"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4 text-xs font-semibold text-text-secondary">
          {errorMsg && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-lg text-[11px]">
              {errorMsg}
            </div>
          )}

          {/* Account Name */}
          <div className="flex flex-col gap-1.5">
            <label className="text-text-muted uppercase tracking-wider text-[10px]">Account Name</label>
            <input
              type="text"
              required
              placeholder="e.g. My Demo Follower 1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="bg-bg-primary border border-bg-border rounded-lg px-3 py-2 text-text-primary focus:border-blue-500 outline-none transition-all"
            />
          </div>

          {/* API Key */}
          <div className="flex flex-col gap-1.5 relative">
            <label className="text-text-muted uppercase tracking-wider text-[10px]">API Key</label>
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                required
                placeholder="Enter Delta India API Key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="w-full bg-bg-primary border border-bg-border rounded-lg pl-3 pr-10 py-2 text-text-primary focus:border-blue-500 outline-none transition-all font-mono"
              />
              <button
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-white"
              >
                {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          {/* API Secret */}
          <div className="flex flex-col gap-1.5 relative">
            <label className="text-text-muted uppercase tracking-wider text-[10px]">API Secret</label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                required
                placeholder="Enter Delta India API Secret"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                className="w-full bg-bg-primary border border-bg-border rounded-lg pl-3 pr-10 py-2 text-text-primary focus:border-blue-500 outline-none transition-all font-mono"
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-white"
              >
                {showSecret ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
          </div>

          {/* Role and Environment Toggles */}
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-text-muted uppercase tracking-wider text-[10px]">Account Role</label>
              <Select
                value={isMaster ? 'master' : 'follower'}
                onChange={(v) => setIsMaster(v === 'master')}
                options={[
                  { value: 'follower', label: 'Follower' },
                  ...(!hasMaster ? [{ value: 'master', label: 'Master' }] : []),
                ]}
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-text-muted uppercase tracking-wider text-[10px]">Environment</label>
              <Select
                value={environment}
                onChange={(v) => setEnvironment(v as 'demo' | 'live')}
                options={[
                  { value: 'demo', label: 'Demo (Testnet)' },
                  { value: 'live', label: 'Live (Production)' },
                ]}
              />
            </div>
          </div>

          {/* Allocation Config (Follower only) */}
          {!isMaster && (
            <div className="border border-bg-border rounded-xl p-4 bg-bg-primary/45 space-y-3">
              <div className="text-[10px] text-blue-400 font-bold uppercase tracking-wider">
                Follower Copy Settings
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className={`flex flex-col gap-1.5 ${allocationMode === 'auto_ratio' ? 'col-span-2' : 'col-span-2 sm:col-span-1'}`}>
                  <label className="text-text-muted uppercase tracking-wider text-[9px]">Copy Mode</label>
                  <Select
                    value={allocationMode}
                    onChange={(mode) => {
                      setAllocationMode(mode as any);
                      if (mode === 'auto_ratio') {
                        setAllocationValue('1.0'); // default fallback value
                      }
                    }}
                    options={[
                      { value: 'auto_ratio', label: 'Auto Balance Ratio (Recommended)' },
                      { value: 'multiplier', label: 'Multiplier (x Size)' },
                    ]}
                  />
                  {allocationMode === 'auto_ratio' && (
                    <p className="text-[10px] text-text-muted font-normal leading-relaxed mt-0.5">
                      Sizes each copy by the follower-to-master balance ratio automatically.
                    </p>
                  )}
                </div>

                {allocationMode !== 'auto_ratio' && (
                  <div className="flex flex-col gap-1.5 col-span-2 sm:col-span-1 animate-slide-in">
                    <label className="text-text-muted uppercase tracking-wider text-[9px]">Multiplier Value</label>
                    <input
                      type="number"
                      step="0.01"
                      min="0.01"
                      required
                      value={allocationValue}
                      onChange={(e) => setAllocationValue(e.target.value)}
                      className="bg-bg-primary border border-bg-border rounded-lg px-3 py-2 text-text-primary outline-none focus:border-blue-500"
                    />
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex gap-3 pt-4 border-t border-bg-border select-none">
            <button
              type="button"
              onClick={onClose}
              disabled={isSaving}
              className="flex-1 py-2 bg-bg-secondary hover:bg-[#2e2e3e] text-text-primary rounded-lg border border-[#2e2e3e] transition-all font-semibold"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving}
              className="flex-1 py-2 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white rounded-lg transition-all flex items-center justify-center gap-1.5 font-semibold"
            >
              {isSaving ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  Saving...
                </>
              ) : (
                'Save Account'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
