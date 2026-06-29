'use client';
import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Users, Activity, FileText, Bell, Zap, ChevronLeft, ChevronRight } from 'lucide-react';
export function Sidebar() {
  const pathname = usePathname();
  const [isCollapsed, setIsCollapsed] = React.useState(false);

  const links = [
    { href: '/positions', label: 'Positions', icon: Activity },
    { href: '/accounts', label: 'Accounts', icon: Users },
    { href: '/trades', label: 'Trades Log', icon: FileText },
    { href: '/alerts', label: 'Alert Feed', icon: Bell },
  ];

  return (
    <aside className={`bg-bg-secondary/80 backdrop-blur-xl border-r border-bg-border flex flex-col h-full select-none transition-all duration-300 ${
      isCollapsed ? 'w-20' : 'w-64'
    }`}>
      {/* Top logo */}
      <div className={`h-16 px-5 border-b border-bg-border flex items-center justify-between ${isCollapsed ? 'flex-col gap-3 justify-center' : ''}`}>
        <div className="flex items-center gap-3">
          <img src="/logo.jpg" alt="Mirror Engine Logo" className="w-9 h-9 rounded-xl object-cover ring-1 ring-bg-border shadow-lg shadow-blue-500/10 shrink-0" />
          {!isCollapsed && (
            <span className="font-bold text-base bg-gradient-to-r from-blue-400 via-sky-300 to-emerald-400 bg-clip-text text-transparent tracking-tight whitespace-nowrap">
              Mirror Engine
            </span>
          )}
        </div>
        {!isCollapsed && (
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-1.5 hover:bg-bg-panel border border-transparent hover:border-bg-border rounded-lg text-text-muted hover:text-text-primary transition-colors"
            title="Collapse Sidebar"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>
        )}
      </div>

      {isCollapsed && (
        <button
          onClick={() => setIsCollapsed(false)}
          className="mx-auto mt-3 p-1.5 hover:bg-bg-panel border border-transparent hover:border-bg-border rounded-lg text-text-muted hover:text-text-primary transition-colors"
          title="Expand Sidebar"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      )}

      {/* Nav links */}
      <nav className={`flex-1 ${isCollapsed ? 'px-2 pt-6' : 'px-4 pt-6'} space-y-1`}>
        {links.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              title={isCollapsed ? link.label : undefined}
              className={`group relative flex items-center gap-3 rounded-xl text-sm transition-all duration-200 ${
                isCollapsed ? 'justify-center px-0 py-3' : 'px-3.5 py-2.5'
              } ${
                isActive
                  ? 'bg-gradient-to-r from-blue-500/15 to-transparent text-text-primary font-semibold shadow-sm'
                  : 'text-text-secondary font-medium hover:text-text-primary hover:bg-bg-panel/60'
              }`}
            >
              {/* Active accent bar */}
              {isActive && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-1 rounded-r-full bg-gradient-to-b from-blue-400 to-emerald-400 shadow-[0_0_8px_rgba(59,130,246,0.6)]" />
              )}
              <Icon className={`w-[18px] h-[18px] shrink-0 transition-colors ${isActive ? 'text-blue-400' : 'text-text-muted group-hover:text-text-secondary'}`} />
              {!isCollapsed && <span className="whitespace-nowrap">{link.label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Footer status */}
      {!isCollapsed && (
        <div className="p-4 border-t border-bg-border">
          <div className="flex items-center gap-2 px-2 py-2 rounded-lg bg-bg-panel/50">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60 animate-ping" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
            </span>
            <span className="text-[11px] font-medium text-text-secondary">Engine Online</span>
          </div>
        </div>
      )}
    </aside>
  );
}
