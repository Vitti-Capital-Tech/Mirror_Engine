'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { Shield, Users, Crown, Activity, AlertTriangle, RefreshCw } from 'lucide-react';

interface AdminUser {
  id: string;
  email?: string;
  role: string;
  total_accounts: number;
  active_accounts: number;
  master_name?: string | null;
  master_live: boolean;
  follower_count: number;
  today_pnl: number;
  copies_today: number;
  copies_filled_today: number;
}
interface Overview {
  totals: {
    users: number; admins: number; accounts: number; masters: number;
    active_listeners: number; orphan_accounts: number;
  };
  users: AdminUser[];
}

function Stat({ label, value, icon: Icon, accent }: { label: string; value: React.ReactNode; icon: any; accent: string }) {
  return (
    <div className="bg-bg-panel border border-bg-border rounded-xl px-4 py-3 flex items-center gap-3">
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${accent}`}>
        <Icon className="w-[18px] h-[18px]" />
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wider text-text-muted font-semibold">{label}</div>
        <div className="text-lg font-bold font-mono text-text-primary">{value}</div>
      </div>
    </div>
  );
}

export default function AdminPage() {
  const { user, loading: authLoading } = useAuth();
  const router = useRouter();
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setData(await api.admin.overview());
      setError('');
    } catch (e: any) {
      setError(e.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authLoading) return;
    if (user && user.role !== 'admin') { router.replace('/positions'); return; }
    if (user) load();
  }, [user, authLoading, load, router]);

  const toggleRole = async (u: AdminUser) => {
    const next = u.role === 'admin' ? 'user' : 'admin';
    if (!confirm(`Change ${u.email || u.id} to "${next}"?`)) return;
    try {
      setBusy(u.id);
      await api.admin.setRole(u.id, next);
      await load();
    } catch (e: any) {
      alert(e.message || 'Failed');
    } finally {
      setBusy(null);
    }
  };

  if (authLoading || (user && user.role !== 'admin')) return null;

  const t = data?.totals;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-bg-border/50 pb-4 select-none">
        <div className="flex items-center gap-2">
          <Shield className="w-4 h-4 text-blue-400" />
          <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Admin — All Tenants</h2>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-2 text-xs font-semibold bg-bg-panel border border-bg-border hover:border-blue-500/50 text-text-secondary hover:text-text-primary px-3 py-1.5 rounded-lg transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
        </button>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3">{error}</div>
      )}

      {/* Totals */}
      {t && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <Stat label="Users" value={t.users} icon={Users} accent="bg-blue-500/15 text-blue-400" />
          <Stat label="Admins" value={t.admins} icon={Shield} accent="bg-purple-500/15 text-purple-400" />
          <Stat label="Accounts" value={t.accounts} icon={Users} accent="bg-sky-500/15 text-sky-400" />
          <Stat label="Masters" value={t.masters} icon={Crown} accent="bg-amber-500/15 text-amber-400" />
          <Stat label="Live Engines" value={t.active_listeners} icon={Activity} accent="bg-emerald-500/15 text-emerald-400" />
          <Stat label="Orphan Accts" value={t.orphan_accounts} icon={AlertTriangle} accent="bg-orange-500/15 text-orange-400" />
        </div>
      )}

      {/* Users table */}
      <div className="bg-bg-secondary/60 border border-bg-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-text-muted border-b border-bg-border">
                <th className="px-4 py-3 font-semibold">User</th>
                <th className="px-4 py-3 font-semibold">Role</th>
                <th className="px-4 py-3 font-semibold">Master</th>
                <th className="px-4 py-3 font-semibold text-right">Followers</th>
                <th className="px-4 py-3 font-semibold text-right">Active</th>
                <th className="px-4 py-3 font-semibold text-right">Today PnL</th>
                <th className="px-4 py-3 font-semibold text-right">Copies (Filled)</th>
                <th className="px-4 py-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {(data?.users || []).map((u) => (
                <tr key={u.id} className="border-b border-bg-border/50 hover:bg-bg-panel/40 transition-colors">
                  <td className="px-4 py-3">
                    <div className="font-medium text-text-primary">{u.email || '—'}</div>
                    <div className="text-[11px] text-text-muted font-mono">{u.id.slice(0, 8)}…</div>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-[11px] font-bold px-2 py-0.5 rounded-full ${
                      u.role === 'admin' ? 'bg-purple-500/15 text-purple-300' : 'bg-bg-panel text-text-secondary'
                    }`}>{u.role}</span>
                  </td>
                  <td className="px-4 py-3">
                    {u.master_name ? (
                      <span className="flex items-center gap-1.5">
                        <Crown className="w-3.5 h-3.5 text-amber-400" />
                        <span className="text-text-primary">{u.master_name}</span>
                        <span className={`w-2 h-2 rounded-full ${u.master_live ? 'bg-emerald-400' : 'bg-text-muted'}`}
                          title={u.master_live ? 'Listener live' : 'Not live'} />
                      </span>
                    ) : <span className="text-text-muted">—</span>}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{u.follower_count}</td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{u.active_accounts}/{u.total_accounts}</td>
                  <td className={`px-4 py-3 text-right font-mono font-semibold ${u.today_pnl > 0 ? 'text-emerald-400' : u.today_pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                    {u.today_pnl.toFixed(2)}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-text-secondary">{u.copies_filled_today}/{u.copies_today}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => toggleRole(u)}
                      disabled={busy === u.id}
                      className="text-[11px] font-semibold px-2.5 py-1 rounded-lg border border-bg-border hover:border-blue-500/50 text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50"
                    >
                      {u.role === 'admin' ? 'Demote' : 'Make admin'}
                    </button>
                  </td>
                </tr>
              ))}
              {!loading && (data?.users || []).length === 0 && (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-text-muted">No users found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
