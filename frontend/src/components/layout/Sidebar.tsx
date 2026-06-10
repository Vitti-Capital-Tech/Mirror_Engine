'use client';
import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Users, Activity, FileText, Bell, Zap, ChevronLeft, ChevronRight } from 'lucide-react';
export function Sidebar() {
  const pathname = usePathname();
  const [isCollapsed, setIsCollapsed] = React.useState(false);

  const links = [
    { href: '/', label: 'Dashboard', icon: LayoutDashboard },
    { href: '/accounts', label: 'Accounts', icon: Users },
    { href: '/positions', label: 'Positions', icon: Activity },
    { href: '/trades', label: 'Trades Log', icon: FileText },
    { href: '/alerts', label: 'Alert Feed', icon: Bell },
  ];

  return (
    <aside className={`bg-bg-secondary border-r border-bg-border flex flex-col h-full select-none transition-all duration-300 ${
      isCollapsed ? 'w-20' : 'w-64'
    }`}>
      {/* Top logo */}
      <div className={`p-5 border-b border-bg-border flex items-center justify-between ${isCollapsed ? 'flex-col gap-3 justify-center' : ''}`}>
        <div className="flex items-center gap-3">
          <img src="/logo.jpg" alt="Mirror Engine Logo" className="w-8 h-8 rounded-lg object-cover border border-bg-border shrink-0" />
          {!isCollapsed && (
            <span className="font-bold text-base bg-gradient-to-r from-blue-400 to-emerald-400 bg-clip-text text-transparent tracking-wide whitespace-nowrap">
              Mirror Engine
            </span>
          )}
        </div>
        <button
          onClick={() => setIsCollapsed(!isCollapsed)}
          className="p-1 hover:bg-bg-panel border border-transparent hover:border-bg-border rounded text-text-secondary hover:text-text-primary transition-colors"
          title={isCollapsed ? "Expand Sidebar" : "Collapse Sidebar"}
        >
          {isCollapsed ? <ChevronRight className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
        </button>
      </div>

      {/* Nav links */}
      <nav className={`flex-1 py-6 space-y-1.5 ${isCollapsed ? 'px-2' : 'px-4'}`}>
        {links.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              title={isCollapsed ? link.label : undefined}
              className={`flex items-center gap-3 rounded-lg text-sm font-medium transition-all duration-200 border-l-2 ${
                isCollapsed ? 'justify-center px-0 py-3' : 'px-4 py-3'
              } ${
                isActive
                  ? 'bg-bg-panel text-text-primary border-blue-500 font-bold'
                  : 'text-text-secondary border-transparent hover:text-text-primary hover:bg-bg-panel/50'
              }`}
            >
              <Icon className={`w-4 h-4 shrink-0 ${isActive ? 'text-blue-400' : 'text-text-muted'}`} />
              {!isCollapsed && <span className="whitespace-nowrap">{link.label}</span>}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
