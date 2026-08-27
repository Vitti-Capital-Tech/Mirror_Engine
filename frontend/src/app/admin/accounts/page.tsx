'use client';
import React, { useEffect, useState, useCallback, useMemo } from 'react';
import toast from 'react-hot-toast';
import { api } from '@/lib/api';
import { AdminHeader, StatusPill, pnlClass } from '@/components/admin/AdminUI';
import { Crown, User, Search, ArrowUpCircle } from 'lucide-react';

/**
 * Every account across all tenants, grouped by owner.
 *
 * Grouped rather than one flat list because the rule being enforced is
 * per-owner: each user has exactly ONE master, and promoting a follower
 * demotes that user's current master. A flat table hides which accounts are
 * competing for that slot; grouping makes the swap obvious before it happens.
 */
export default function AdminAccounts() {
  const [accounts, setAccounts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [q, setQ] = useState('');
  const [promoting, setPromoting] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setAccounts(await api.admin.accounts());
      setError('');
    } catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const filtered = accounts.filter(a => {
    if (!q) return true;
    const s = q.toLowerCase();
    return (a.name || '').toLowerCase().includes(s) || (a.owner_email || '').toLowerCase().includes(s);
  });

  /** owner_email -> that owner's accounts, master first. */
  const byOwner = useMemo(() => {
    const groups: Record<string, any[]> = {};
    for (const a of filtered) {
      const key = a.owner_email || '—';
      (groups[key] ||= []).push(a);
    }
    for (const list of Object.values(groups)) {
      list.sort((x, y) => Number(y.is_master) - Number(x.is_master)
        || (x.name || '').localeCompare(y.name || ''));
    }
    return Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0]));
  }, [filtered]);

  const doPromote = async (acc: any, currentMaster: any) => {
    setPromoting(acc.id);
    try {
      await api.accounts.promote(acc.id);
      toast.success(
        currentMaster
          ? `"${acc.name}" is now the master. "${currentMaster.name}" is a follower.`
          : `"${acc.name}" is now the master.`
      );
      await load();
    } catch (e: any) {
      toast.error(e.message || 'Failed to change master.');
    } finally {
      setPromoting(null);
    }
  };

  /**
   * Confirm before swapping. This re-points where a live trading account's
   * orders originate — and it changes it for someone else's capital — so it is
   * deliberately not a one-click action, and the dialog names both sides of the
   * swap rather than just the account being promoted.
   */
  const confirmPromote = (acc: any, currentMaster: any) => {
    toast((t) => (
      <div className="flex flex-col gap-2">
        <span className="text-text-primary text-sm font-semibold">Change master for {acc.owner_email}?</span>
        <span className="text-text-secondary text-xs leading-relaxed">
          <strong className="text-emerald-400">{acc.name}</strong> becomes the master.
          {currentMaster && (
            <> <strong className="text-amber-300">{currentMaster.name}</strong> is demoted to follower.</>
          )}
          <br />
          Live feeds are rewired immediately: the new master starts copying its fills
          out, and the old one switches to being copied to.
        </span>
        <div className="flex gap-2 justify-end mt-1">
          <button onClick={() => toast.dismiss(t.id)}
            className="px-2.5 py-1 text-xs rounded-md bg-bg-secondary text-text-secondary hover:text-white border border-bg-border">
            Cancel
          </button>
          <button onClick={() => { toast.dismiss(t.id); doPromote(acc, currentMaster); }}
            className="px-2.5 py-1 text-xs rounded-md bg-amber-500 hover:bg-amber-600 text-black font-semibold">
            Make master
          </button>
        </div>
      </div>
    ), { duration: 12000 });
  };

  return (
    <div>
      <AdminHeader onRefresh={load} refreshing={loading}>
        <div className="flex items-center gap-2 bg-bg-panel border border-bg-border rounded-lg px-3 py-1.5">
          <Search className="w-3.5 h-3.5 text-text-muted" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name / owner"
            className="bg-transparent outline-none text-xs text-text-primary placeholder:text-text-muted w-44" />
        </div>
      </AdminHeader>

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {loading && accounts.length === 0 && (
        <div className="card-premium p-10 text-center text-text-muted">
          <span className="inline-flex items-center gap-2">
            <span className="inline-block w-4 h-4 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
            Loading accounts…
          </span>
        </div>
      )}
      {!loading && byOwner.length === 0 && (
        <div className="card-premium p-10 text-center text-text-muted">No accounts found.</div>
      )}

      <div className="space-y-4">
        {byOwner.map(([owner, list]) => {
          const currentMaster = list.find(a => a.is_master) || null;
          return (
            <div key={owner} className="card-premium overflow-hidden">
              <div className="flex items-center justify-between px-4 py-3 border-b border-bg-border">
                <span className="font-semibold text-text-primary text-sm truncate">{owner}</span>
                <span className="text-[11px] text-text-muted">
                  {currentMaster
                    ? <>master <span className="text-amber-300 font-semibold">{currentMaster.name}</span></>
                    : <span className="text-rose-400 font-semibold">no master set</span>}
                  {' · '}{list.length} account{list.length === 1 ? '' : 's'}
                </span>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
                      <th className="px-4 py-2.5 font-bold">Account</th>
                      <th className="px-4 py-2.5 font-bold">Role</th>
                      <th className="px-4 py-2.5 font-bold">Status</th>
                      <th className="px-4 py-2.5 font-bold">Env</th>
                      <th className="px-4 py-2.5 font-bold text-right">Balance</th>
                      <th className="px-4 py-2.5 font-bold text-right">Today PnL</th>
                      <th className="px-4 py-2.5 font-bold text-right">Master</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {list.map((a) => (
                      <tr key={a.id} className="hover:bg-bg-panel/40 transition-colors">
                        <td className="px-4 py-3 font-medium text-text-primary">
                          {a.name}
                          {a.live && (
                            <span className="ml-2 text-[9px] font-bold text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-1.5 py-0.5 rounded">
                              LIVE
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3">
                          {a.is_master ? (
                            <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-amber-300">
                              <Crown className="w-3.5 h-3.5" /> Master
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-text-secondary">
                              <User className="w-3.5 h-3.5" /> Follower
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3"><StatusPill status={a.status} /></td>
                        <td className="px-4 py-3">
                          <span className={`text-[11px] font-semibold ${a.environment === 'live' ? 'text-emerald-400' : 'text-amber-400'}`}>
                            {a.environment}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-text-secondary">
                          {a.balance != null ? Number(a.balance).toFixed(2) : '—'}
                          {a.allocated_balance != null && (
                            <span className="block text-[10px] text-text-muted">alloc {Number(a.allocated_balance).toFixed(0)}</span>
                          )}
                        </td>
                        <td className={`px-4 py-3 text-right font-mono font-semibold ${pnlClass(Number(a.today_pnl) || 0)}`}>
                          {a.today_pnl != null ? Number(a.today_pnl).toFixed(2) : '—'}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {a.is_master ? (
                            <span className="text-[11px] text-text-muted">current</span>
                          ) : (
                            <button
                              onClick={() => confirmPromote(a, currentMaster)}
                              disabled={promoting === a.id}
                              title={currentMaster
                                ? `Make "${a.name}" the master and demote "${currentMaster.name}"`
                                : `Make "${a.name}" the master`}
                              className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-1 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20 transition-colors disabled:opacity-40 cursor-pointer"
                            >
                              <ArrowUpCircle className="w-3.5 h-3.5" />
                              {promoting === a.id ? 'Switching…' : 'Make master'}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </div>

      {byOwner.length > 0 && (
        <p className="text-[11px] text-text-muted mt-4 leading-relaxed">
          Each user has exactly one master. Promoting a follower demotes that user&apos;s
          current master in the same operation and rewires both live feeds — the new
          master starts copying its fills out, the old one starts being copied to.
          Open positions are not touched.
        </p>
      )}
    </div>
  );
}
