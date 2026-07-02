'use client';
import React, { useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { MirrorGraphic } from '@/components/auth/AuthShell';
import { Radio, Scale, ShieldCheck, TrendingUp, ArrowRight, Zap } from 'lucide-react';

const FEATURES = [
  { icon: Radio, anim: 'icon-pulse', title: 'Real-time mirroring', desc: 'Master fills replicate to every follower in milliseconds — positions, closes, and bracket orders.' },
  { icon: Scale, anim: 'icon-wobble', title: 'Balance-ratio sizing', desc: 'Each follower is sized precisely to its own capital, floored to whole contracts.' },
  { icon: ShieldCheck, anim: 'icon-float', title: 'Encrypted & isolated', desc: 'API keys encrypted at rest, with strict per-user data isolation across tenants.' },
];

export default function Landing() {
  const { user, loading, isAuthenticated } = useAuth();
  const router = useRouter();

  // Logged-in visitors skip the landing and go to their dashboard.
  useEffect(() => {
    if (loading) return;
    if (isAuthenticated) router.replace(user?.role === 'admin' ? '/admin' : '/positions');
  }, [loading, isAuthenticated, user, router]);

  if (!loading && isAuthenticated) return null;

  return (
    <div className="relative min-h-screen w-screen overflow-x-hidden bg-bg-primary text-text-primary">
      {/* Ambient orbs */}
      <div className="orb-a pointer-events-none absolute -top-32 -left-24 w-[28rem] h-[28rem] rounded-full blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.20), transparent 65%)' }} />
      <div className="orb-b pointer-events-none absolute top-40 -right-24 w-[32rem] h-[32rem] rounded-full blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(139,92,246,0.16), transparent 65%)' }} />

      {/* Nav */}
      <header className="relative z-10 max-w-6xl mx-auto flex items-center justify-between px-6 py-5">
        <div className="flex items-center gap-3">
          <div className="relative w-9 h-9 rounded-xl overflow-hidden ring-1 ring-bg-border shadow-lg shadow-blue-500/20">
            <img src="/logo.jpg" alt="Mirror Engine" className="w-full h-full object-cover" />
            <span className="pointer-events-none absolute top-0 left-0 h-full w-2/3 bg-gradient-to-r from-transparent via-white/55 to-transparent animate-shimmer" />
          </div>
          <span className="font-extrabold text-lg bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">Mirror Engine</span>
        </div>
        <Link href="/login" className="text-sm font-semibold text-text-secondary hover:text-text-primary px-4 py-2 rounded-lg border border-bg-border hover:border-blue-500/50 transition-colors">
          Sign in
        </Link>
      </header>

      {/* Hero */}
      <main className="relative z-10 max-w-6xl mx-auto px-6">
        <div className="grid lg:grid-cols-2 gap-10 items-center pt-10 lg:pt-16">
          {/* Copy */}
          <div className="animate-fade-in">
            <div className="inline-flex items-center gap-1.5 rounded-full border border-bg-border bg-bg-panel/50 px-3 py-1 text-[11px] font-semibold text-text-secondary mb-6">
              <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> Copy-trading for Delta Exchange
            </div>
            <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight leading-[1.1]">
              One master.<br />
              <span className="bg-gradient-to-r from-sky-300 to-blue-500 bg-clip-text text-transparent">Infinite mirrors.</span>
            </h1>
            <p className="text-base text-text-secondary mt-5 leading-relaxed max-w-lg">
              Trade once on your master account and watch it replicate across every follower —
              sized to the last contract, in real time, fully automated.
            </p>
            <div className="flex flex-wrap items-center gap-3 mt-8">
              <Link href="/signup"
                className="group relative inline-flex items-center gap-2 py-3 px-6 rounded-xl bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white text-sm font-semibold shadow-lg shadow-blue-600/25 transition-all overflow-hidden">
                <span className="pointer-events-none absolute top-0 left-0 h-full w-1/3 bg-gradient-to-r from-transparent via-white/25 to-transparent -translate-x-full group-hover:translate-x-[400%] transition-transform duration-700" />
                Get started <ArrowRight className="w-4 h-4" />
              </Link>
              <Link href="/login"
                className="inline-flex items-center gap-2 py-3 px-6 rounded-xl border border-bg-border hover:border-blue-500/50 bg-bg-panel/40 text-sm font-semibold text-text-primary transition-colors">
                Sign in
              </Link>
            </div>
            <div className="flex items-center gap-2 mt-6 text-[11px] text-text-muted">
              <Zap className="w-3.5 h-3.5 text-blue-400" /> Live on Delta Exchange India · Master → follower mirroring
            </div>
          </div>

          {/* Animated graphic */}
          <div className="relative animate-fade-in">
            <div className="card-premium p-8 lg:p-10 flex items-center justify-center">
              <MirrorGraphic />
            </div>
          </div>
        </div>

        {/* Features */}
        <section className="grid sm:grid-cols-3 gap-4 mt-16 lg:mt-24">
          {FEATURES.map((f) => {
            const Icon = f.icon;
            return (
              <div key={f.title} className="card-premium card-hover p-6">
                <div className="flex items-center justify-center w-11 h-11 rounded-xl bg-gradient-to-br from-blue-500/20 to-blue-500/5 ring-1 ring-blue-500/25 shadow-lg shadow-blue-500/10 mb-4">
                  <Icon className={`w-5 h-5 text-blue-300 ${f.anim}`} strokeWidth={2} />
                </div>
                <h3 className="text-sm font-bold text-text-primary">{f.title}</h3>
                <p className="text-xs text-text-muted mt-1.5 leading-relaxed">{f.desc}</p>
              </div>
            );
          })}
        </section>

        {/* CTA strip */}
        <section className="card-premium mt-8 mb-16 p-8 lg:p-10 flex flex-col sm:flex-row items-center justify-between gap-5 text-center sm:text-left">
          <div>
            <h2 className="text-xl font-bold text-text-primary tracking-tight">Ready to mirror your trades?</h2>
            <p className="text-sm text-text-muted mt-1">Set up your master and followers in minutes.</p>
          </div>
          <Link href="/signup"
            className="inline-flex items-center gap-2 py-3 px-6 rounded-xl bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white text-sm font-semibold shadow-lg shadow-blue-600/25 transition-all shrink-0">
            Get started <ArrowRight className="w-4 h-4" />
          </Link>
        </section>
      </main>

      {/* Footer */}
      <footer className="relative z-10 border-t border-bg-border">
        <div className="max-w-6xl mx-auto px-6 py-6 flex flex-col sm:flex-row items-center justify-between gap-3 text-[11px] text-text-muted">
          <span>
            © {new Date().getFullYear()}{' '}
            <a href="https://vitti.capital" target="_blank" rel="noopener noreferrer" className="text-text-secondary hover:text-blue-400 font-medium transition-colors">Vitti Capital</a>{' '}· Mirror Engine
          </span>
          <div className="flex items-center gap-4">
            <Link href="/login" className="hover:text-text-secondary transition-colors">Sign in</Link>
            <Link href="/signup" className="hover:text-text-secondary transition-colors">Create account</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
