'use client';
import React, { useEffect, useState, useCallback } from 'react';
import Link from 'next/link';
import { api } from '@/lib/api';
import { AdminHeader, StatCard, RoleBadge, pnlClass } from '@/components/admin/AdminUI';
import {
  LayoutGrid, Users, Crown, Wallet, Activity, AlertTriangle, ArrowRight, Server, Database, Radio,
} from 'lucide-react';

export default function AdminOverview() {
  const [data, setData] = useState<any>(null);
  const [system, setSystem] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setLoading(true);
      const [ov, sys] = await Promise.all([api.admin.overview(), api.dashboard.system().catch(() => null)]);
      setData(ov); setSystem(sys); setError('');
    } catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const t = data?.totals;
  const users = (data?.users || []).slice().sort((a: any, b: any) => (b.copies_today || 0) - (a.copies_today || 0));

  return (
    <div>
      <AdminHeader icon={LayoutGrid} title="Admin Overview" subtitle="Platform-wide activity across all tenants" onRefresh={load} refreshing={loading} />

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {/* Stat grid */}
      {t && (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-6">
          <StatCard label="Users" value={t.users} icon={Users} accent="bg-blue-500/15 text-blue-400" hint={`${t.admins} admin${t.admins === 1 ? '' : 's'}`} />
          <StatCard label="Accounts" value={t.accounts} icon={Wallet} accent="bg-sky-500/15 text-sky-400" />
          <StatCard label="Masters" value={t.masters} icon={Crown} accent="bg-amber-500/15 text-amber-400" />
          <StatCard label="Live Engines" value={t.active_listeners} icon={Activity} accent="bg-emerald-500/15 text-emerald-400" />
          <StatCard label="Orphan Accts" value={t.orphan_accounts} icon={AlertTriangle} accent="bg-orange-500/15 text-orange-400" />
          <StatCard label="Copies Today" value={(data.users || []).reduce((s: number, u: any) => s + (u.copies_today || 0), 0)} icon={Radio} accent="bg-violet-500/15 text-violet-400" />
        </div>
      )}

      {/* System health strip */}
      {system && (
        <div className="card-premium p-4 mb-6 flex flex-wrap items-center gap-x-8 gap-y-3">
          <div className="text-[11px] uppercase tracking-wider text-text-muted font-semibold">System Health</div>
          <Health ok={system.supabase_ok} icon={Database} label="Supabase" />
          <Health ok={system.redis_ok} icon={Server} label="Redis" />
          <Health ok={system.master_ws_connected} icon={Radio} label="Master WS" />
          <div className="flex items-center gap-2 text-xs text-text-secondary">
            <span className="text-text-muted">Env:</span>
            <span className={`font-semibold ${system.environment === 'live' ? 'text-emerald-400' : 'text-amber-400'}`}>{system.environment}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-text-secondary">
            <span className="text-text-muted">WS conns:</span>
            <span className="font-mono text-text-primary">{system.total_ws_connections}</span>
          </div>
        </div>
      )}

      {/* Top users */}
      <div className="card-premium overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-bg-border">
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Most active users today</h2>
          <Link href="/admin/users" className="text-xs font-semibold text-blue-400 hover:text-blue-300 flex items-center gap-1">
            All users <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-text-muted border-b border-bg-border/60">
                <th className="px-4 py-2.5 font-semibold">User</th>
                <th className="px-4 py-2.5 font-semibold">Master</th>
                <th className="px-4 py-2.5 font-semibold text-right">Followers</th>
                <th className="px-4 py-2.5 font-semibold text-right">Today PnL</th>
                <th className="px-4 py-2.5 font-semibold text-right">Copies</th>
              </tr>
            </thead>
            <tbody>
              {users.slice(0, 6).map((u: any) => (
                <tr key={u.id} className="border-b border-bg-border/40 hover:bg-bg-panel/40 transition-colors">
                  <td className="px-4 py-2.5">
                    <span className="text-text-primary font-medium">{u.email || '—'}</span>{' '}
                    <RoleBadge role={u.role} />
                  </td>
                  <td className="px-4 py-2.5">
                    {u.master_name ? (
                      <span className="flex items-center gap-1.5">
                        <Crown className="w-3.5 h-3.5 text-amber-400" />
                        <span className="text-text-secondary">{u.master_name}</span>
                        <span className={`w-2 h-2 rounded-full ${u.master_live ? 'bg-emerald-400' : 'bg-text-muted'}`} />
                      </span>
                    ) : <span className="text-text-muted">—</span>}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-secondary">{u.follower_count}</td>
                  <td className={`px-4 py-2.5 text-right font-mono font-semibold ${pnlClass(u.today_pnl)}`}>{Number(u.today_pnl).toFixed(2)}</td>
                  <td className="px-4 py-2.5 text-right font-mono text-text-secondary">{u.copies_filled_today}/{u.copies_today}</td>
                </tr>
              ))}
              {!loading && users.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-10 text-center text-text-muted">No users yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Health({ ok, icon: Icon, label }: { ok: boolean; icon: any; label: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <Icon className="w-4 h-4 text-text-muted" />
      <span className="text-text-secondary">{label}</span>
      <span className={`w-2 h-2 rounded-full ${ok ? 'bg-emerald-400' : 'bg-red-400'}`} />
    </div>
  );
}
