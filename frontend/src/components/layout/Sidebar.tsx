'use client';
import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Users, Activity, FileText, Bell, Zap } from 'lucide-react';
export function Sidebar() {
  const pathname = usePathname();

  const links = [
    { href: '/', label: 'Dashboard', icon: LayoutDashboard },
    { href: '/accounts', label: 'Accounts', icon: Users },
    { href: '/positions', label: 'Positions', icon: Activity },
    { href: '/trades', label: 'Trades Log', icon: FileText },
    { href: '/alerts', label: 'Alert Feed', icon: Bell },
  ];

  return (
    <aside className="w-64 bg-bg-secondary border-r border-bg-border flex flex-col h-full select-none">
      {/* Top logo */}
      <div className="p-6 border-b border-bg-border flex items-center">
        <span className="font-bold text-lg bg-gradient-to-r from-blue-400 to-emerald-400 bg-clip-text text-transparent tracking-wide">
          Mirror Engine
        </span>
      </div>

      {/* Nav links */}
      <nav className="flex-1 px-4 py-6 space-y-1.5">
        {links.map((link) => {
          const Icon = link.icon;
          const isActive = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 border-l-2 ${
                isActive
                  ? 'bg-bg-panel text-text-primary border-blue-500 font-bold'
                  : 'text-text-secondary border-transparent hover:text-text-primary hover:bg-bg-panel/50'
              }`}
            >
              <Icon className={`w-4 h-4 ${isActive ? 'text-blue-400' : 'text-text-muted'}`} />
              {link.label}
            </Link>
          );
        })}
      </nav>

    </aside>
  );
}
