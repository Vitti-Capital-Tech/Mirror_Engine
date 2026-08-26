'use client';
import React, { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Send, Users } from 'lucide-react';
import toast from 'react-hot-toast';
import { api } from '@/lib/api';
import { ComparisonView } from '@/components/comparison/ComparisonView';
import { useReportStatus } from '@/hooks/useComparison';
import { Loader } from '@/components/shared/Loader';

/**
 * The admin view of the comparison: pick a tenant, see their master vs their
 * followers. Read-only, like the rest of the admin console — an admin can see
 * every tenant's fills and cannot change any of them, including which of their
 * accounts is the master.
 */
export default function AdminComparisonPage() {
  const [users, setUsers] = useState<any[]>([]);
  const [ownerId, setOwnerId] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const { data: status } = useReportStatus(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.admin.overview();
        // Only tenants with a master can be compared against anything.
        const withMaster = (res.users || []).filter((u: any) => u.master_name);
        setUsers(withMaster);
        if (withMaster.length) setOwnerId(withMaster[0].id);
      } catch (e: any) {
        setError(e.message || 'Failed to load tenants');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const selected = useMemo(() => users.find(u => u.id === ownerId), [users, ownerId]);

  const sendAll = async () => {
    setSending(true);
    try {
      const res = await api.comparison.send();
      if (res.sent) {
        toast.success(`Report sent for ${res.messages} of ${res.owners} tenant(s)`);
      } else {
        toast.error(res.reason || 'Nothing was sent');
      }
      (res.failures || []).forEach((f: string) => toast.error(f));
    } catch (e: any) {
      toast.error(e.message || 'Send failed');
    } finally {
      setSending(false);
    }
  };

  if (loading) return <Loader label="Loading tenants…" />;

  return (
    <div className="space-y-5">
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      {/* Daily-report health. If the schedule is off or Telegram isn't wired up,
          the desk would otherwise just quietly stop receiving the doc. */}
      {status && (
        <div className="card-premium p-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-[11px]">
          <span className="text-text-muted">
            Daily report{' '}
            <span className={status.enabled ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>
              {status.enabled ? 'scheduled' : 'disabled'}
            </span>
            {status.enabled && <> at <span className="font-mono text-text-secondary">{status.send_at_ist} IST</span></>}
          </span>
          <span className="text-text-muted">
            Telegram{' '}
            <span className={status.telegram_configured ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>
              {status.telegram_configured ? 'connected' : 'not configured'}
            </span>
          </span>
          <span className="text-text-muted">
            Today ({status.today_ist}){' '}
            <span className={status.sent_marker_today ? 'text-emerald-400 font-semibold' : 'text-text-secondary'}>
              {status.sent_marker_today ? 'already sent' : 'not sent yet'}
            </span>
          </span>
          <div className="flex-1" />
          <button onClick={sendAll} disabled={sending}
            title="Send today's report for every tenant to Telegram now"
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-blue-500/15 border border-blue-500/30 text-blue-300 hover:bg-blue-500/25 transition-colors disabled:opacity-50">
            <Send className="w-3.5 h-3.5" /> Send all tenants now
          </button>
        </div>
      )}

      {users.length === 0 ? (
        <div className="card-premium p-10 text-center text-text-muted text-sm">
          <AlertTriangle className="w-5 h-5 mx-auto mb-2 text-amber-400" />
          No tenant has a master account yet — there is nothing to compare.
        </div>
      ) : (
        <>
          <div className="card-premium p-4 flex flex-wrap items-center gap-3">
            <Users className="w-4 h-4 text-text-muted" />
            <label className="text-[11px] font-semibold text-text-muted uppercase tracking-[0.1em]">
              Tenant
            </label>
            <select value={ownerId} onChange={e => setOwnerId(e.target.value)}
              className="bg-bg-panel border border-bg-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-blue-500/50 min-w-[220px]">
              {users.map(u => (
                <option key={u.id} value={u.id}>
                  {u.email} — {u.master_name} + {u.follower_count} follower{u.follower_count === 1 ? '' : 's'}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-muted">View only — an admin cannot change a tenant's accounts.</span>
          </div>

          {ownerId && (
            <ComparisonView key={ownerId} ownerId={ownerId} ownerLabel={selected?.email} />
          )}
        </>
      )}
    </div>
  );
}
