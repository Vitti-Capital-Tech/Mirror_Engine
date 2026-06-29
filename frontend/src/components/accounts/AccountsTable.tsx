'use client';
import React, { useState } from 'react';
import {
  usePauseAccount,
  useResumeAccount,
  useDeleteAccount,
  useResetAccount,
  useTestAccount,
  usePromoteAccount
} from '@/hooks/useAccounts';
import { StatusBadge } from '../shared/StatusBadge';
import { Play, Pause, RotateCcw, Trash2, ShieldCheck, RefreshCw, Crown } from 'lucide-react';
import toast from 'react-hot-toast';

// A confirm dialog rendered as a toast (replaces window.confirm)
function confirmToast(message: string, onConfirm: () => void) {
  toast((t) => (
    <div className="flex flex-col gap-2">
      <span className="text-text-primary">{message}</span>
      <div className="flex gap-2 justify-end">
        <button
          onClick={() => toast.dismiss(t.id)}
          className="px-2.5 py-1 text-xs rounded-md bg-bg-secondary text-text-secondary hover:text-white border border-bg-border"
        >
          Cancel
        </button>
        <button
          onClick={() => { toast.dismiss(t.id); onConfirm(); }}
          className="px-2.5 py-1 text-xs rounded-md bg-blue-500 hover:bg-blue-600 text-white font-semibold"
        >
          Confirm
        </button>
      </div>
    </div>
  ), { duration: 8000 });
}

export function AccountsTable({ accounts = [], isLoading }: { accounts?: any[]; isLoading: boolean }) {
  const pauseAcc = usePauseAccount();
  const resumeAcc = useResumeAccount();
  const deleteAcc = useDeleteAccount();
  const resetAcc = useResetAccount();
  const testAcc = useTestAccount();
  const promoteAcc = usePromoteAccount();

  const [testingId, setTestingId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="card-premium p-6 h-64 animate-pulse flex flex-col justify-between">
        <div className="h-4 bg-bg-secondary rounded w-full mb-4"></div>
        <div className="flex-1 space-y-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="grid grid-cols-8 gap-4">
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-1"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
              <div className="h-8 bg-bg-secondary rounded col-span-2"></div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const handleTestConnection = async (id: string) => {
    setTestingId(id);
    const tId = toast.loading('Testing connection...');
    try {
      const res = await testAcc.mutateAsync(id);
      if (res.success) {
        toast.success(res.message, { id: tId });
      } else {
        toast.error(res.message, { id: tId });
      }
    } catch (e: any) {
      toast.error(e.message || 'API error', { id: tId });
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = (id: string, name: string) => {
    confirmToast(`Delete account "${name}"?`, () => {
      deleteAcc.mutate(id, {
        onSuccess: () => toast.success(`Account "${name}" deleted.`),
        onError: (e: any) => toast.error(e.message || 'Failed to delete account.'),
      });
    });
  };

  const handlePromote = (id: string, name: string) => {
    confirmToast(`Make "${name}" the master? The current master becomes a follower.`, () => {
      promoteAcc.mutate(id, {
        onSuccess: () => toast.success(`"${name}" is now the master account.`),
        onError: (e: any) => toast.error(e.message || 'Failed to promote account.'),
      });
    });
  };

  const handlePause = (id: string, name: string) => {
    pauseAcc.mutate(id, {
      onSuccess: () => toast.success(`"${name}" paused.`),
      onError: (e: any) => toast.error(e.message || 'Failed to pause.'),
    });
  };

  const handleResume = (id: string, name: string) => {
    resumeAcc.mutate(id, {
      onSuccess: () => toast.success(`"${name}" is now active.`),
      onError: (e: any) => toast.error(e.message || 'Failed to resume.'),
    });
  };

  const getAllocationText = (acc: any) => {
    if (acc.is_master) return 'N/A (Master)';
    const mode = acc.allocation_mode;
    const val = Number(acc.allocation_value || 0);
    if (mode === 'auto_ratio') return 'Auto Balance Ratio';
    if (mode === 'fixed') return `Fixed: ${val} Contract${val > 1 ? 's' : ''}`;
    if (mode === 'multiplier') return `Multiplier: ${val}x`;
    if (mode === 'capital_pct') return `Capital: ${val}%`;
    return '-';
  };

  const masters = accounts.filter(a => a.is_master);
  const followers = accounts.filter(a => !a.is_master);

  const renderTable = (list: any[], isMasterTable: boolean) => {
    if (list.length === 0) {
      return (
        <div className="py-6 text-center text-text-muted text-xs border border-dashed border-bg-border rounded-xl select-none">
          No {isMasterTable ? 'Master' : 'Follower'} accounts configured.
        </div>
      );
    }

    return (
      <div className="card-premium p-5 overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
              <th className="py-3">Name</th>
              <th>Environment</th>
              {!isMasterTable && <th>Allocation</th>}
              <th className="text-right pr-6">Balance</th>
              <th className="text-right pr-6">Today's PnL</th>
              <th className="pl-12">Status</th>
              <th className="text-right py-3 pr-2">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border/50 font-medium">
            {list.map((acc) => {
              const isTesting = testingId === acc.id;
              const pnl = Number(acc.today_pnl || 0);

              return (
                <tr key={acc.id} className="hover:bg-bg-secondary/20 transition-colors">
                  <td className="py-4 font-bold text-text-primary pl-1">{acc.name}</td>
                  <td className="py-4 select-none font-bold">
                    <span className={acc.environment === 'live' ? 'text-amber-400' : 'text-text-secondary'}>
                      {acc.environment?.toUpperCase()}
                    </span>
                  </td>
                  {!isMasterTable && <td className="py-4 text-text-secondary">{getAllocationText(acc)}</td>}
                  <td className="py-4 text-right pr-6 font-mono text-text-primary">
                    {acc.balance !== null ? `${Number(acc.balance).toFixed(2)} USDT` : '-'}
                  </td>
                  <td className={`py-4 text-right pr-6 font-mono ${pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                    {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} USDT
                  </td>
                  <td className="py-4 pl-12 select-none">
                    <StatusBadge status={acc.status} />
                  </td>
                  <td className="py-4 text-right space-x-2.5 whitespace-nowrap pr-2">
                    {/* Test Connection */}
                    <button
                      onClick={() => handleTestConnection(acc.id)}
                      disabled={isTesting}
                      title="Test API Connection"
                      className="p-2 bg-bg-secondary hover:bg-bg-border/60 hover:text-white text-text-secondary rounded-lg border border-bg-border shadow-sm transition-all duration-200 disabled:opacity-50 inline-flex items-center justify-center cursor-pointer"
                    >
                      {isTesting ? (
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                      ) : (
                        <ShieldCheck className="w-3.5 h-3.5" />
                      )}
                    </button>

                    {/* Pause / Resume */}
                    {acc.status === 'active' ? (
                      <button
                        onClick={() => handlePause(acc.id, acc.name)}
                        title="Pause Copying"
                        className="p-2 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 rounded-lg border border-amber-500/20 shadow-sm transition-all duration-200 inline-flex items-center justify-center cursor-pointer"
                      >
                        <Pause className="w-3.5 h-3.5" />
                      </button>
                    ) : (
                      <button
                        onClick={() => handleResume(acc.id, acc.name)}
                        title="Resume Copying"
                        disabled={acc.status === 'circuit_break'}
                        className="p-2 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-lg border border-emerald-500/20 shadow-sm transition-all duration-200 disabled:opacity-30 disabled:hover:bg-emerald-500/10 inline-flex items-center justify-center cursor-pointer"
                      >
                        <Play className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Reset failure counter */}
                    {acc.status === 'circuit_break' && (
                      <button
                        onClick={() => resetAcc.mutate(acc.id)}
                        title="Reset Circuit Breaker"
                        className="p-2 bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 rounded-lg border border-blue-500/20 shadow-sm transition-all duration-200 animate-pulse inline-flex items-center justify-center cursor-pointer"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Promote follower to master */}
                    {!isMasterTable && (
                      <button
                        onClick={() => handlePromote(acc.id, acc.name)}
                        disabled={promoteAcc.isPending}
                        title="Make Master Account"
                        className="p-2 bg-purple-500/10 hover:bg-purple-500/20 text-purple-400 rounded-lg border border-purple-500/20 shadow-sm transition-all duration-200 disabled:opacity-40 inline-flex items-center justify-center cursor-pointer"
                      >
                        <Crown className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Delete */}
                    <button
                      onClick={() => handleDelete(acc.id, acc.name)}
                      title="Delete Account"
                      className="p-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg border border-red-500/20 shadow-sm transition-all duration-200 inline-flex items-center justify-center cursor-pointer"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  return (
    <div className="space-y-8">
      {/* Master Account Section */}
      <div className="space-y-3">
        <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-wider select-none">
          Master Account
        </h3>
        {renderTable(masters, true)}
      </div>

      {/* Follower Accounts Section */}
      <div className="space-y-3">
        <h3 className="text-[11px] font-bold text-text-muted uppercase tracking-wider select-none">
          Follower Accounts ({followers.length})
        </h3>
        {renderTable(followers, false)}
      </div>
    </div>
  );
}
