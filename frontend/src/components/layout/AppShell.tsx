'use client';
import React, { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { Sidebar } from '@/components/layout/Sidebar';
import { MobileNav } from '@/components/layout/MobileNav';
import { TopBar } from '@/components/layout/TopBar';
import { LogsConsole } from '@/components/layout/LogsConsole';
import { useAuth } from '@/context/AuthContext';

const PUBLIC_ROUTES = ['/', '/login', '/signup', '/auth/callback'];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthenticated, loading, user, signingOut } = useAuth();
  const isPublic = PUBLIC_ROUTES.includes(pathname);

  useEffect(() => {
    if (loading) return;
    if (!isAuthenticated && !isPublic) router.replace('/login');
    if (isAuthenticated && isPublic) router.replace(user?.role === 'admin' ? '/admin' : '/positions');
  }, [loading, isAuthenticated, isPublic, pathname, router, user]);

  // Public pages (login/signup) render without the app chrome.
  if (isPublic) return <>{children}</>;

  // While resolving session, or redirecting an unauthenticated user, show nothing.
  if (loading || !isAuthenticated) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-bg-primary">
        <div className="flex flex-col items-center gap-3">
          <div className="relative w-12 h-12 rounded-2xl overflow-hidden ring-1 ring-bg-border shadow-lg shadow-blue-500/20">
            <img src="/logo.jpg" alt="Mirror Engine" className="w-full h-full object-cover" />
            <span className="pointer-events-none absolute top-0 left-0 h-full w-2/3 bg-gradient-to-r from-transparent via-white/55 to-transparent animate-shimmer" />
          </div>
          <div className="flex items-center gap-2">
            <div className="w-4 h-4 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
            <span className="text-xs text-text-muted">{signingOut ? 'Signing out…' : 'Loading…'}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden text-text-primary">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <TopBar />
        <main className="flex-1 overflow-auto p-4 sm:p-6 pb-24 lg:pb-6 bg-bg-primary">{children}</main>
        <div className="hidden lg:block"><LogsConsole /></div>
      </div>
      <MobileNav />
    </div>
  );
}
