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
      <div className="bg-bg-panel border border-bg-border rounded-xl p-5 overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="text-text-muted border-b border-bg-border uppercase font-bold text-[10px] select-none">
              <th className="py-3">Name</th>
              <th>Environment</th>
              {!isMasterTable && <th>Allocation</th>}
              <th className="text-right">Balance</th>
              {!isMasterTable && <th className="text-right">Today's PnL</th>}
              <th className="pl-6">Status</th>
              <th className="text-right py-3 pr-2">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-bg-border/50 font-medium">
            {list.map((acc) => {
              const isTesting = testingId === acc.id;
              const pnl = Number(acc.today_pnl || 0);

              return (
                <tr key={acc.id} className="hover:bg-bg-secondary/20 transition-colors">
                  <td className="py-3.5 font-bold text-text-primary pl-1">{acc.name}</td>
                  <td className="py-3.5 select-none font-bold">
                    <span className={acc.environment === 'live' ? 'text-amber-400' : 'text-text-secondary'}>
                      {acc.environment?.toUpperCase()}
                    </span>
                  </td>
                  {!isMasterTable && <td className="py-3.5 text-text-secondary">{getAllocationText(acc)}</td>}
                  <td className="py-3.5 text-right font-mono text-text-primary">
                    {acc.balance !== null ? `${Number(acc.balance).toFixed(2)} USDT` : '-'}
                  </td>
                  {!isMasterTable && (
                    <td className={`py-3.5 text-right font-mono ${pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                      {pnl >= 0 ? '+' : ''}{pnl.toFixed(2)} USDT
                    </td>
                  )}
                  <td className="py-3.5 pl-6 select-none">
                    <StatusBadge status={acc.status} />
                  </td>
                  <td className="py-3.5 text-right space-x-2 whitespace-nowrap pr-2">
                    {/* Connection Test feedback */}
                    {testResult && testResult.id === acc.id && (
                      <span className={`inline-block mr-2 text-[10px] font-bold select-none ${testResult.ok ? 'text-emerald-400' : 'text-red-400'}`}>
                        {testResult.msg}
                      </span>
                    )}

                    {/* Test Connection */}
                    <button
                      onClick={() => handleTestConnection(acc.id)}
                      disabled={isTesting}
                      title="Test API Connection"
                      className="p-1.5 bg-bg-secondary hover:bg-bg-border/60 text-text-secondary hover:text-white rounded-lg transition-all border border-bg-border disabled:opacity-50 inline-flex items-center justify-center"
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
                        className="p-1.5 bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 rounded-lg transition-all border border-amber-500/20 inline-flex items-center justify-center"
                      >
                        <Pause className="w-3.5 h-3.5" />
                      </button>
                    ) : (
                      <button
                        onClick={() => resumeAcc.mutate(acc.id)}
                        title="Resume Copying"
                        disabled={acc.status === 'circuit_break'}
                        className="p-1.5 bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 rounded-lg transition-all border border-emerald-500/20 disabled:opacity-30 disabled:hover:bg-emerald-500/10 inline-flex items-center justify-center"
                      >
                        <Play className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Reset failure counter */}
                    {acc.status === 'circuit_break' && (
                      <button
                        onClick={() => resetAcc.mutate(acc.id)}
                        title="Reset Circuit Breaker"
                        className="p-1.5 bg-blue-500/10 hover:bg-blue-500/20 text-blue-400 rounded-lg transition-all border border-blue-500/20 animate-pulse inline-flex items-center justify-center"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                      </button>
                    )}

                    {/* Delete */}
                    <button
                      onClick={() => handleDelete(acc.id, acc.name)}
                      title="Delete Account"
                      className="p-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-lg transition-all border border-red-500/20 inline-flex items-center justify-center"
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
          Master Account ({masters.length})
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
