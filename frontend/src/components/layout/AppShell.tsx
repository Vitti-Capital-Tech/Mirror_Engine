'use client';
import React, { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { LogsConsole } from '@/components/layout/LogsConsole';
import { useAuth } from '@/context/AuthContext';

const PUBLIC_ROUTES = ['/', '/login', '/signup', '/auth/callback'];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { isAuthenticated, loading, user } = useAuth();
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
        <div className="w-6 h-6 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden text-text-primary">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <TopBar />
        <main className="flex-1 overflow-auto p-6 bg-bg-primary">{children}</main>
        <LogsConsole />
      </div>
    </div>
  );
}
