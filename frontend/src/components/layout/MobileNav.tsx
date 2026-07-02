'use client';
import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Users, Activity, FileText, Bell, Wallet } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';

export function MobileNav() {
  const pathname = usePathname();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const traderLinks = [
    { href: '/positions', label: 'Positions', icon: Activity },
    { href: '/accounts', label: 'Accounts', icon: Users },
    { href: '/trades', label: 'Trades', icon: FileText },
    { href: '/alerts', label: 'Alerts', icon: Bell },
  ];
  const adminLinks = [
    { href: '/admin', label: 'Positions', icon: Activity },
    { href: '/admin/users', label: 'Users', icon: Users },
    { href: '/admin/accounts', label: 'Accounts', icon: Wallet },
    { href: '/admin/trades', label: 'Trades', icon: FileText },
    { href: '/admin/alerts', label: 'Alerts', icon: Bell },
  ];
  const links = isAdmin ? adminLinks : traderLinks;

  return (
    <nav className="lg:hidden fixed bottom-0 inset-x-0 z-40 bg-bg-secondary/95 backdrop-blur-xl border-t border-bg-border flex items-stretch justify-around"
      style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
      {links.map((link) => {
        const Icon = link.icon;
        const isActive = pathname === link.href;
        return (
          <Link key={link.href} href={link.href}
            className={`flex-1 flex flex-col items-center justify-center gap-1 py-2.5 text-[10px] font-semibold transition-colors ${
              isActive ? 'text-blue-400' : 'text-text-muted hover:text-text-secondary'
            }`}>
            <Icon className={`w-5 h-5 ${isActive ? 'text-blue-400' : 'text-text-muted'}`} />
            <span className="leading-none">{link.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
