'use client';
import React from 'react';
import { usePathname } from 'next/navigation';
import { useAlerts } from '@/hooks/useAlerts';
import { Bell, Sun, Moon } from 'lucide-react';
import Link from 'next/link';
import { useSocket } from '@/hooks/useSocket';

export function TopBar() {
  const pathname = usePathname();
  const { data: alerts = [] } = useAlerts({ is_resolved: false });
  const activeAlertsCount = alerts.length;
  const { isConnected } = useSocket();

  const [theme, setTheme] = React.useState('dark');

  React.useEffect(() => {
    const activeTheme = document.documentElement.classList.contains('light') ? 'light' : 'dark';
    setTheme(activeTheme);
  }, []);

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

  const titles: Record<string, string> = {
    '/': 'Trading Overview',
    '/accounts': 'Accounts Management',
    '/positions': 'Live Positions',
    '/trades': 'Trade Audit Log',
    '/alerts': 'System Alert Feed',
  };

  const title = titles[pathname] || 'Mirror Engine';

  return (
    <header className="h-16 border-b border-bg-border bg-bg-panel/70 backdrop-blur-xl flex items-center justify-between px-6 select-none shrink-0">
      <div className="flex flex-col">
        <h1 className="text-base font-bold text-text-primary tracking-tight leading-tight">{title}</h1>
        <span className="text-[11px] text-text-muted font-medium">Delta Exchange India · Copy Trading</span>
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

        {/* Alerts Bell notification */}
        <Link 
          href="/alerts" 
          className="relative p-1.5 text-text-secondary hover:text-white hover:bg-bg-secondary rounded-lg transition-all duration-200 border border-transparent hover:border-bg-border"
        >
          <Bell className="w-4 h-4" />
          {activeAlertsCount > 0 && (
            <span className="absolute top-0 right-0 w-3.5 h-3.5 bg-red-500 text-white rounded-full text-[8px] font-bold flex items-center justify-center animate-pulse">
              {activeAlertsCount}
            </span>
          )}
        </Link>
      </div>
    </header>
  );
}
