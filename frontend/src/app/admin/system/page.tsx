'use client';
import React, { useEffect, useState, useCallback } from 'react';
import { api } from '@/lib/api';
import { AdminHeader } from '@/components/admin/AdminUI';
import { Server, Database, Radio, Activity, Clock, Globe } from 'lucide-react';

function fmtUptime(sec?: number) {
  if (!sec) return '—';
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return [d && `${d}d`, h && `${h}h`, `${m}m`].filter(Boolean).join(' ');
}

export default function AdminSystem() {
  const [sys, setSys] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try { setLoading(true); setSys(await api.dashboard.system()); setError(''); }
    catch (e: any) { setError(e.message || 'Failed to load'); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 15000); return () => clearInterval(id); }, [load]);

  const services = sys ? [
    { icon: Database, label: 'Supabase (Postgres)', ok: sys.supabase_ok },
    { icon: Server, label: 'Redis', ok: sys.redis_ok },
    { icon: Radio, label: 'Master WebSocket', ok: sys.master_ws_connected },
  ] : [];

  return (
    <div>
      <AdminHeader icon={Server} title="System Health" subtitle="Live status of backend services and engines" onRefresh={load} refreshing={loading} />

      {error && <div className="bg-red-500/10 border border-red-500/30 text-red-300 text-sm rounded-lg px-4 py-3 mb-5">{error}</div>}

      {/* Service status */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-6">
        {services.map((s) => {
          const Icon = s.icon;
          return (
            <div key={s.label} className="card-premium p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${s.ok ? 'bg-emerald-500/12 text-emerald-400' : 'bg-red-500/12 text-red-400'}`}>
                  <Icon className="w-5 h-5" />
                </div>
                <div className="text-sm font-semibold text-text-primary">{s.label}</div>
              </div>
              <span className={`inline-flex items-center gap-1.5 text-[11px] font-bold px-2.5 py-1 rounded-full ${s.ok ? 'bg-emerald-500/12 text-emerald-300' : 'bg-red-500/12 text-red-300'}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${s.ok ? 'bg-emerald-400' : 'bg-red-400'}`} />
                {s.ok ? 'Operational' : 'Down'}
              </span>
            </div>
          );
        })}
      </div>

      {/* Metrics */}
      {sys && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Metric icon={Globe} label="Environment" value={sys.environment} accent={sys.environment === 'live' ? 'text-emerald-400' : 'text-amber-400'} />
          <Metric icon={Activity} label="WS Connections" value={sys.total_ws_connections} />
          <Metric icon={Clock} label="Uptime" value={fmtUptime(sys.uptime_seconds)} />
          <Metric icon={Radio} label="Master WS" value={sys.master_ws_connected ? 'Connected' : 'Offline'} accent={sys.master_ws_connected ? 'text-emerald-400' : 'text-red-400'} />
        </div>
      )}
    </div>
  );
}

function Metric({ icon: Icon, label, value, accent = 'text-text-primary' }: { icon: any; label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="card-premium p-4">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-text-muted font-semibold mb-2">
        <Icon className="w-3.5 h-3.5" /> {label}
      </div>
      <div className={`text-lg font-bold font-mono ${accent}`}>{value}</div>
    </div>
  );
}
