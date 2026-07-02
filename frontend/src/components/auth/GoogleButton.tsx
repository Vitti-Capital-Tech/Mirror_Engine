'use client';
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase, googleEnabled } from '@/lib/supabaseClient';
import { useAuth } from '@/context/AuthContext';
import { api } from '@/lib/api';

export function GoogleButton() {
  const [busy, setBusy] = useState(false);
  const { setSession } = useAuth();
  const router = useRouter();

  // Receive the token from the OAuth popup (works when embedded in an iframe:
  // Google can't run inside an iframe, so we open it top-level in a popup and
  // the callback posts the session back here).
  useEffect(() => {
    const handler = async (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return;
      if (e.data?.type !== 'mirror-auth' || !e.data.token) return;
      const token = e.data.token as string;
      setSession(token);
      let me: any = null;
      try { me = await api.auth.me(); } catch { /* ignore */ }
      if (me) setSession(token, { id: me.id, email: me.email, role: me.role });
      setBusy(false);
      router.replace(me?.role === 'admin' ? '/admin' : '/positions');
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [router, setSession]);

  if (!googleEnabled) return null;

  const go = async () => {
    setBusy(true);
    const { data, error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: `${window.location.origin}/auth/callback`, skipBrowserRedirect: true },
    });
    if (error || !data?.url) { setBusy(false); return; }

    // Open Google at the top level (popup) so it isn't blocked inside an iframe.
    const popup = window.open(data.url, 'mirror-google-oauth', 'width=480,height=680');
    if (!popup) {
      // Popup blocked — fall back to a full top-level navigation, escaping the iframe.
      try { (window.top || window).location.href = data.url; }
      catch { window.location.href = data.url; }
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={go}
        disabled={busy}
        className="w-full flex items-center justify-center gap-2.5 py-2.5 rounded-xl bg-bg-primary border border-bg-border hover:border-bg-border/80 hover:bg-bg-secondary/60 text-sm font-semibold text-text-primary transition-all disabled:opacity-60"
      >
        <svg width="16" height="16" viewBox="0 0 48 48" aria-hidden="true">
          <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 12.955 4 4 12.955 4 24s8.955 20 20 20 20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"/>
          <path fill="#FF3D00" d="M6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4 16.318 4 9.656 8.337 6.306 14.691z"/>
          <path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238C29.211 35.091 26.715 36 24 36c-5.202 0-9.619-3.317-11.283-7.946l-6.522 5.025C9.505 39.556 16.227 44 24 44z"/>
          <path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303c-.792 2.237-2.231 4.166-4.087 5.571.001-.001.002-.001.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"/>
        </svg>
        {busy ? 'Waiting for Google…' : 'Continue with Google'}
      </button>

      <div className="flex items-center gap-3 my-4">
        <span className="h-px flex-1 bg-bg-border" />
        <span className="text-[10px] text-text-muted uppercase tracking-wider">or</span>
        <span className="h-px flex-1 bg-bg-border" />
      </div>
    </>
  );
}
