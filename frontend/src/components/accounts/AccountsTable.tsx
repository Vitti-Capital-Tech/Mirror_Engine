'use client';
import React, { useState } from 'react';
import { 
  usePauseAccount, 
  useResumeAccount, 
  useDeleteAccount, 
  useResetAccount, 
  useTestAccount 
} from '@/hooks/useAccounts';
import { StatusBadge } from '../shared/StatusBadge';
import { Play, Pause, RotateCcw, Trash2, ShieldCheck, RefreshCw } from 'lucide-react';

export function AccountsTable({ accounts = [], isLoading }: { accounts?: any[]; isLoading: boolean }) {
  const pauseAcc = usePauseAccount();
  const resumeAcc = useResumeAccount();
  const deleteAcc = useDeleteAccount();
  const resetAcc = useResetAccount();
  const testAcc = useTestAccount();

  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id: string; msg: string; ok: boolean } | null>(null);

  if (isLoading) {
    return (
      <div className="bg-bg-panel border border-bg-border rounded-xl p-6 h-64 animate-pulse flex flex-col justify-between">
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
    setTestResult(null);
    try {
      const res = await testAcc.mutateAsync(id);
      setTestResult({ id, msg: res.message, ok: res.success });
    } catch (e: any) {
      setTestResult({ id, msg: e.message || 'API error', ok: false });
    } finally {
      setTestingId(null);
    }
  };

  const handleDelete = (id: string, name: string) => {
    if (window.confirm(`Are you sure you want to delete account: ${name}?`)) {
      deleteAcc.mutate(id);
    }
  };

  const getAllocationText = (acc: any) => {
    if (acc.is_master) return 'N/A (Master)';
    const mode = acc.allocation_mode;
    const val = Number(acc.allocation_value || 0);
    if (mode === 'fixed') return `Fixed: ${val} Contract${val > 1 ? 's' : ''}`;
    if (mode === 'multiplier') return `Multiplier: ${val}x`;
    if (mode === 'capital_pct') return `Capital: ${val}%`;
    return '-';
  };

  return (
    <div className="bg-bg-panel border border-bg-border rounded-xl p-6">
      <div className="overflow-x-auto">
        {accounts.length === 0 ? (
          <div className="py-12 text-center text-text-muted text-xs select-none">
            No accounts configured. Click "Add Account" to get started.
          </div>
        ) : (
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
                <th className="py-3">Name</th>
                <th>Role</th>
                <th>Environment</th>
                <th>Allocation</th>
                <th className="text-right">Balance</th>
                <th className="text-right">Today's PnL</th>
                <th>Status</th>
                <th className="text-right py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-bg-border/50 font-medium">
              {accounts.map((acc) => {
                const isMaster = acc.is_master;
                const isTesting = testingId === acc.id;
                const pnl = Number(acc.today_pnl || 0);
                
                return (
                  <tr key={acc.id} className="hover:bg-bg-secondary/40 transition-colors">
                    <td className="py-3.5">
                      <div className="font-bold text-text-primary">{acc.name}</div>
                      <div className="text-[10px] text-text-muted mt-0.5 font-mono">
                        Key: {acc.api_key}
                      </div>
                    </td>
                    <td className="py-3.5 select-none">
                      {isMaster ? (
                        <span className="bg-blue-500/10 text-blue-400 border border-blue-500/20 px-2 py-0.5 rounded text-[10px] font-bold">
                          MASTER
                        </span>
                      ) : (
                        <span className="bg-purple-500/10 text-purple-400 border border-purple-500/20 px-2 py-0.5 rounded text-[10px] font-bold">
                          FOLLOWER
                        </span>
                      )}
                    </td>
                    <td className="py-3.5 select-none font-bold">
                      <span className={acc.environment === 'live' ? 'text-amber-400' : 'text-text-secondary'}>
                        {acc.environment?.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-3.5 text-text-secondary">{getAllocationText(acc)}</td>
                    <td className="py-3.5 text-right font-mono text-text-primary">
                      {acc.balance !== null ? `${Number(acc.balance).toFixed(2)} USDT` : '-'}
                    </td>
                    <td className={`py-3.5 text-right font-mono ${pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                      {isMaster ? '-' : `${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} USDT`}
                    </td>
                    <td className="py-3.5 select-none">
                      <StatusBadge status={acc.status} />
                    </td>
                    <td className="py-3.5 text-right space-x-1.5 whitespace-nowrap">
                      {/* Connection Test feedback */}
                      {testResult && testResult.id === acc.id && (
                        <span className={`inline-block mr-2 text-[10px] font-semibold select-none ${testResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                          {testResult.msg}
                        </span>
                      )}

                      {/* Test Connection */}
                      <button
                        onClick={() => handleTestConnection(acc.id)}
                        disabled={isTesting}
                        title="Test API Connection"
                        className="p-1.5 bg-bg-secondary hover:bg-[#2e2e3e] text-text-secondary hover:text-white rounded-lg transition-all border border-[#2e2e3e] disabled:opacity-50"
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
                          onClick={() => pauseAcc.mutate(acc.id)}
                          title="Pause Copying"
                          className="p-1.5 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 rounded-lg transition-all border border-amber-500/20"
                        >
                          <Pause className="w-3.5 h-3.5" />
                        </button>
                      ) : (
                        <button
                          onClick={() => resumeAcc.mutate(acc.id)}
                          title="Resume Copying"
                          disabled={acc.status === 'circuit_break'}
                          className="p-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-lg transition-all border border-emerald-500/20 disabled:opacity-30 disabled:hover:bg-emerald-500/10"
                        >
                          <Play className="w-3.5 h-3.5" />
                        </button>
                      )}

                      {/* Reset failure counter */}
                      {acc.status === 'circuit_break' && (
                        <button
                          onClick={() => resetAcc.mutate(acc.id)}
                          title="Reset Circuit Breaker"
                          className="p-1.5 bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 rounded-lg transition-all border border-blue-500/20 animate-pulse"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                      )}

                      {/* Delete */}
                      <button
                        onClick={() => handleDelete(acc.id, acc.name)}
                        title="Delete Account"
                        className="p-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg transition-all border border-red-500/20"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
