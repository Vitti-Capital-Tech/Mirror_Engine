'use client';
import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase } from '@/lib/supabaseClient';
import { setToken, api } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';

export default function AuthCallback() {
  const router = useRouter();
  const { setSession } = useAuth();
  const [error, setError] = useState('');

  useEffect(() => {
    if (!supabase) { router.replace('/login'); return; }
    let done = false;

    const finish = async (token?: string | null) => {
      if (done || !token) return;
      done = true;
      setToken(token);
      // Resolve the full profile (incl. role) and push it into auth state BEFORE
      // navigating, so the app sees us as authenticated and doesn't bounce to /login.
      let me: any = null;
      try { me = await api.auth.me(); } catch { /* ignore */ }
      if (me) setSession(token, { id: me.id, email: me.email, role: me.role });
      else setSession(token);
      router.replace(me?.role === 'admin' ? '/admin' : '/positions');
    };

    const { data: sub } = supabase.auth.onAuthStateChange((_e: any, session: any) => {
      finish(session?.access_token);
    });
    supabase.auth.getSession().then(({ data }: any) => finish(data.session?.access_token));

    const t = setTimeout(() => {
      if (!done) setError('Google sign-in failed. Please try again.');
    }, 8000);

    return () => { sub.subscription.unsubscribe(); clearTimeout(t); };
  }, [router, setSession]);

  return (
    <div className="relative min-h-screen w-screen flex items-center justify-center bg-bg-primary overflow-hidden">
      {/* Ambient orbs */}
      <div className="orb-a pointer-events-none absolute -top-32 -left-24 w-[26rem] h-[26rem] rounded-full blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.18), transparent 65%)' }} />
      <div className="orb-b pointer-events-none absolute -bottom-32 -right-24 w-[28rem] h-[28rem] rounded-full blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(139,92,246,0.14), transparent 65%)' }} />

      <div className="relative z-10 flex flex-col items-center animate-fade-in">
        {!error ? (
          <>
            {/* Logo with pulsing ring */}
            <div className="relative mb-6">
              <span className="absolute inset-0 rounded-2xl bg-blue-500/30 blur-xl animate-pulse" />
              <div className="relative w-16 h-16 rounded-2xl overflow-hidden ring-1 ring-bg-border shadow-lg shadow-blue-500/25">
                <img src="/logo.jpg" alt="Mirror Engine" className="w-full h-full object-cover" />
                <span className="pointer-events-none absolute top-0 left-0 h-full w-2/3 bg-gradient-to-r from-transparent via-white/55 to-transparent animate-shimmer" />
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <div className="w-4 h-4 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
              <span className="text-sm font-semibold text-text-primary">Signing you in</span>
            </div>
            <p className="text-xs text-text-muted mt-2">Securely connecting your account…</p>
          </>
        ) : (
          <div className="card-premium p-8 text-center max-w-[340px]">
            <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-red-500/10 ring-1 ring-red-500/20 mb-4 mx-auto">
              <span className="text-red-400 text-xl font-bold">!</span>
            </div>
            <h1 className="text-base font-bold text-text-primary">Sign-in failed</h1>
            <p className="text-xs text-text-muted mt-1.5 mb-5">{error}</p>
            <button onClick={() => router.replace('/login')}
              className="w-full py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-colors">
              Back to login
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
