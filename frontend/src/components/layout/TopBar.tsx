'use client';
import React from 'react';
import { usePathname } from 'next/navigation';
import { useAlerts } from '@/hooks/useAlerts';
import { Bell, Sun, Moon, AlertTriangle, ShieldAlert, Info, CheckCircle, ArrowRight } from 'lucide-react';
import Link from 'next/link';
import { useSocket } from '@/hooks/useSocket';

function relTime(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export function TopBar() {
  const pathname = usePathname();
  const { data: alerts = [] } = useAlerts({ is_resolved: false });
  const activeAlertsCount = alerts.length;
  const { isConnected } = useSocket();

  const [theme, setTheme] = React.useState('dark');
  const [bellOpen, setBellOpen] = React.useState(false);
  const [readIds, setReadIds] = React.useState<Set<string>>(new Set());
  const bellRef = React.useRef<HTMLDivElement>(null);

  // Load persisted read notification ids
  React.useEffect(() => {
    try {
      const raw = localStorage.getItem('readAlertIds');
      if (raw) setReadIds(new Set(JSON.parse(raw)));
    } catch {}
  }, []);

  const persistRead = (s: Set<string>) => {
    setReadIds(new Set(s));
    try { localStorage.setItem('readAlertIds', JSON.stringify([...s])); } catch {}
  };
  const markRead = (id: string) => {
    if (readIds.has(id)) return;
    const s = new Set(readIds);
    s.add(id);
    persistRead(s);
  };
  const markAllRead = () => {
    const s = new Set(readIds);
    alerts.forEach((a: any) => s.add(a.id));
    persistRead(s);
  };
  const unreadCount = alerts.filter((a: any) => !readIds.has(a.id)).length;

  React.useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (bellRef.current && !bellRef.current.contains(e.target as Node)) setBellOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  React.useEffect(() => {
    const activeTheme = document.documentElement.classList.contains('light') ? 'light' : 'dark';
    setTheme(activeTheme);
  }, []);

  const alertIcon = (level: string) => {
    const l = level?.toLowerCase();
    if (l === 'critical') return { Icon: ShieldAlert, chip: 'bg-red-500/10 text-red-400', label: 'text-red-400', bar: 'bg-red-500' };
    if (l === 'error') return { Icon: AlertTriangle, chip: 'bg-orange-500/10 text-orange-400', label: 'text-orange-400', bar: 'bg-orange-500' };
    if (l === 'warning') return { Icon: AlertTriangle, chip: 'bg-amber-500/10 text-amber-400', label: 'text-amber-400', bar: 'bg-amber-500' };
    return { Icon: Info, chip: 'bg-blue-500/10 text-blue-400', label: 'text-blue-400', bar: 'bg-blue-500' };
  };

  const toggleTheme = () => {
    const nextTheme = theme === 'dark' ? 'light' : 'dark';
    if (nextTheme === 'dark') {
      document.documentElement.classList.add('dark');
      document.documentElement.classList.remove('light');
    } else {
      document.documentElement.classList.add('light');
      document.documentElement.classList.remove('dark');
    }
    localStorage.setItem('theme', nextTheme);
    setTheme(nextTheme);
  };

  const meta: Record<string, { title: string; subtitle: string }> = {
    '/': { title: 'Live Positions', subtitle: 'Real-time positions and orders mirrored from your master account' },
    '/accounts': { title: 'Accounts', subtitle: 'Manage your master and follower exchange connections' },
    '/positions': { title: 'Live Positions', subtitle: 'Real-time positions and orders mirrored from your master account' },
    '/trades': { title: 'Trade Log', subtitle: 'Full audit trail of every copied trade and its fills' },
    '/alerts': { title: 'Alerts', subtitle: 'Slippage, sync and connection events that need your attention' },
  };

  const { title, subtitle } = meta[pathname] || { title: 'Mirror Engine', subtitle: 'Delta Exchange India · Copy Trading' };

  return (
    <header className="relative z-30 h-16 border-b border-bg-border bg-bg-panel/70 backdrop-blur-xl flex items-center justify-between px-6 select-none shrink-0">
      <div className="flex items-center gap-3.5">
        <span className="h-9 w-1 rounded-full bg-gradient-to-b from-blue-400 to-emerald-400" />
        <div className="flex flex-col">
          <h1 className="text-[17px] font-bold text-text-primary tracking-tight leading-tight">{title}</h1>
          <span className="text-[11px] text-text-muted font-medium leading-tight mt-0.5">{subtitle}</span>
        </div>
      </div>

      <div className="flex items-center gap-2.5">
        {/* Connection Status Indicator */}
        <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full text-[11px] font-bold tracking-wide select-none border transition-colors ${
          isConnected
            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25 shadow-[0_0_16px_-4px_rgba(16,185,129,0.4)]'
            : 'bg-red-500/10 text-red-400 border-red-500/25'
        }`}>
          <span className="relative flex h-2 w-2">
            {isConnected && <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60 animate-ping" />}
            <span className={`relative inline-flex h-2 w-2 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
          </span>
          {isConnected ? 'LIVE' : 'DISCONNECTED'}
        </div>

        {/* Theme Toggle */}
        <button
          onClick={toggleTheme}
          title={theme === 'dark' ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
          className="p-1.5 text-text-secondary hover:text-white hover:bg-bg-secondary rounded-lg transition-all duration-200 border border-transparent hover:border-bg-border"
        >
          {theme === 'dark' ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4 text-slate-500" />}
        </button>

        {/* Alerts Bell notification dropdown */}
        <div className="relative" ref={bellRef}>
          <button
            onClick={() => setBellOpen((o) => !o)}
            className={`relative p-1.5 rounded-lg transition-all duration-200 border ${
              bellOpen ? 'text-white bg-bg-secondary border-bg-border' : 'text-text-secondary hover:text-white hover:bg-bg-secondary border-transparent hover:border-bg-border'
            }`}
          >
            <Bell className="w-4 h-4" />
            {unreadCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 min-w-[15px] h-[15px] px-1 bg-red-500 text-white rounded-full text-[8px] font-bold flex items-center justify-center ring-2 ring-bg-panel">
                {unreadCount > 9 ? '9+' : unreadCount}
              </span>
            )}
          </button>

          {bellOpen && (
            <div className="absolute right-0 mt-2 w-80 rounded-xl border border-bg-border bg-bg-elevated shadow-2xl shadow-black/50 overflow-hidden z-50 animate-fade-in">
              {/* Header */}
              <div className="flex items-center justify-between px-4 py-3 border-b border-bg-border">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold text-text-primary">Notifications</span>
                  {unreadCount > 0 && (
                    <span className="text-[9px] font-bold bg-red-500/15 text-red-400 px-1.5 py-0.5 rounded-full">{unreadCount} new</span>
                  )}
                </div>
                {unreadCount > 0 && (
                  <button
                    onClick={markAllRead}
                    className="text-[10px] font-semibold text-blue-400 hover:text-blue-300 transition-colors"
                  >
                    Mark all read
                  </button>
                )}
              </div>

              {/* List */}
              <div className="max-h-80 overflow-auto p-1.5 space-y-0.5">
                {alerts.length === 0 ? (
                  <div className="flex flex-col items-center text-center py-10 px-4">
                    <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-emerald-500/10 mb-2">
                      <CheckCircle className="w-5 h-5 text-emerald-400" />
                    </div>
                    <p className="text-xs font-semibold text-text-primary">All clear</p>
                    <p className="text-[11px] text-text-muted mt-0.5">No active alerts.</p>
                  </div>
                ) : (
                  alerts.slice(0, 6).map((a: any) => {
                    const { Icon, chip } = alertIcon(a.level);
                    const unread = !readIds.has(a.id);
                    return (
                      <button
                        key={a.id}
                        onClick={() => markRead(a.id)}
                        className={`w-full text-left flex items-start gap-3 px-2.5 py-2.5 rounded-lg transition-colors ${
                          unread ? 'bg-blue-500/[0.06] hover:bg-blue-500/[0.11]' : 'hover:bg-bg-secondary/50'
                        }`}
                      >
                        <span className={`flex items-center justify-center w-8 h-8 rounded-lg shrink-0 ${chip}`}>
                          <Icon className="w-4 h-4" />
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className={`text-[11px] leading-snug line-clamp-2 ${unread ? 'text-text-primary font-semibold' : 'text-text-secondary font-medium'}`}>{a.message}</p>
                          <span className="text-[10px] text-text-muted">{relTime(a.created_at)} ago{a.account_name ? ` · ${a.account_name}` : ''}</span>
                        </div>
                        {unread && <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-blue-400 shrink-0" />}
                      </button>
                    );
                  })
                )}
              </div>

              {/* Footer */}
              <Link
                href="/alerts"
                onClick={() => setBellOpen(false)}
                className="group/link flex items-center justify-center gap-1.5 py-2.5 text-[11px] font-semibold text-blue-400 hover:bg-bg-secondary/40 border-t border-bg-border transition-colors"
              >
                View all alerts
                <ArrowRight className="w-3 h-3 transition-transform group-hover/link:translate-x-0.5" />
              </Link>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
