'use client';
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '@/lib/supabaseClient';
import { setToken, api } from '@/lib/api';

export default function AuthCallback() {
  const router = useRouter();
  const [error, setError] = useState('');

  useEffect(() => {
    if (!supabase) { router.replace('/login'); return; }
    let done = false;
    const finish = async (token?: string | null) => {
      if (done) return;
      if (token) {
        done = true;
        setToken(token);
        let role: string | undefined;
        try { role = (await api.auth.me())?.role; } catch { /* ignore */ }
        router.replace(role === 'admin' ? '/admin' : '/positions');
      }
    };

    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      finish(session?.access_token);
    });

    supabase.auth.getSession().then(({ data }) => {
      finish(data.session?.access_token);
    });

    // If nothing resolves in a few seconds, bounce back to login.
    const t = setTimeout(() => {
      if (!done) { setError('Google sign-in failed. Please try again.'); }
    }, 6000);

    return () => { sub.subscription.unsubscribe(); clearTimeout(t); };
  }, [router]);

  return (
    <div className="min-h-screen w-screen flex items-center justify-center bg-bg-primary">
      <div className="flex flex-col items-center gap-3">
        {!error ? (
          <>
            <div className="w-6 h-6 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
            <p className="text-xs text-text-muted">Completing sign-in…</p>
          </>
        ) : (
          <>
            <p className="text-xs text-red-400">{error}</p>
            <button onClick={() => router.replace('/login')} className="text-xs text-blue-400 font-semibold">Back to login</button>
          </>
        )}
      </div>
    </div>
  );
}
