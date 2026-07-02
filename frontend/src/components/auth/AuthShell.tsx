'use client';
import React, { useEffect, useState } from 'react';
import { Radio, Scale, ShieldCheck, TrendingUp, Eye, EyeOff, Lock, AlertCircle } from 'lucide-react';

const FEATURES = [
  { icon: Radio, title: 'Real-time mirroring', desc: 'Master fills copy to every follower in milliseconds.' },
  { icon: Scale, title: 'Balance-ratio sizing', desc: 'Each account sized precisely to its own capital.' },
  { icon: ShieldCheck, title: 'Encrypted & isolated', desc: 'Keys encrypted at rest, per-user data isolation.' },
];

/** Auto-rotating feature highlight that "rolls" to the next every few seconds. */
function RotatingFeatures() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((p) => (p + 1) % FEATURES.length), 3800);
    return () => clearInterval(id);
  }, []);
  const f = FEATURES[i];
  const Icon = f.icon;
  return (
    <div className="mt-9">
      {/* Fixed-height stage so the layout doesn't jump as text rolls */}
      <div className="relative h-[76px] overflow-hidden">
        <div key={i} className="absolute inset-0 flex items-start gap-3.5 animate-slide-in">
          <div className="mt-0.5 flex items-center justify-center w-11 h-11 rounded-2xl bg-gradient-to-br from-blue-500/20 to-blue-500/5 ring-1 ring-blue-500/25 shrink-0 shadow-lg shadow-blue-500/10">
            <Icon className="w-5 h-5 text-blue-300" strokeWidth={2} />
          </div>
          <div>
            <div className="text-[15px] font-semibold text-text-primary">{f.title}</div>
            <div className="text-xs text-text-muted mt-1 leading-relaxed max-w-xs">{f.desc}</div>
          </div>
        </div>
      </div>
      {/* Progress dots */}
      <div className="flex items-center gap-1.5 mt-4">
        {FEATURES.map((_, idx) => (
          <button
            key={idx}
            onClick={() => setI(idx)}
            aria-label={`Feature ${idx + 1}`}
            className={`h-1.5 rounded-full transition-all duration-500 ${
              idx === i ? 'w-7 bg-blue-400' : 'w-1.5 bg-text-muted/40 hover:bg-text-muted/70'
            }`}
          />
        ))}
      </div>
    </div>
  );
}

/** Premium split-screen wrapper for the auth pages. */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen w-screen flex bg-bg-primary overflow-hidden">
      {/* Left — brand / hero (hidden on small screens) */}
      <aside className="relative hidden lg:flex flex-col justify-between w-[46%] max-w-[620px] p-12 xl:p-16 overflow-hidden border-r border-bg-border">
        <div className="pointer-events-none absolute inset-0 opacity-90"
          style={{
            backgroundImage:
              'radial-gradient(700px circle at 15% 10%, rgba(59,130,246,0.14), transparent 55%),' +
              'radial-gradient(600px circle at 90% 90%, rgba(139,92,246,0.12), transparent 55%)',
          }} />
        <div className="pointer-events-none absolute inset-0 opacity-[0.15]"
          style={{
            backgroundImage:
              'linear-gradient(to right, rgba(148,163,184,0.12) 1px, transparent 1px),' +
              'linear-gradient(to bottom, rgba(148,163,184,0.12) 1px, transparent 1px)',
            backgroundSize: '44px 44px',
            maskImage: 'radial-gradient(circle at 30% 30%, black, transparent 75%)',
          }} />

        <div className="relative z-10 flex items-center gap-3">
          <div className="relative w-11 h-11 rounded-2xl overflow-hidden ring-1 ring-bg-border shadow-lg shadow-blue-500/20">
            <img src="/logo.jpg" alt="Mirror Engine" className="w-full h-full object-cover" />
            <span className="pointer-events-none absolute top-0 left-0 h-full w-2/3 bg-gradient-to-r from-transparent via-white/55 to-transparent animate-shimmer" />
          </div>
          <span className="font-extrabold text-xl bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">
            Mirror Engine
          </span>
        </div>

        <div className="relative z-10 max-w-md">
          <div className="inline-flex items-center gap-1.5 rounded-full border border-bg-border bg-bg-panel/50 px-3 py-1 text-[11px] font-semibold text-text-secondary mb-6">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> Copy-trading for Delta Exchange
          </div>
          <h1 className="text-3xl xl:text-4xl font-extrabold text-text-primary leading-tight tracking-tight">
            One master.<br />
            <span className="bg-gradient-to-r from-sky-300 to-blue-500 bg-clip-text text-transparent">Infinite mirrors.</span>
          </h1>
          <p className="text-sm text-text-secondary mt-4 leading-relaxed">
            Trade once on your master account and watch it replicate across every follower,
            sized to the last contract — automatically.
          </p>

          <RotatingFeatures />
        </div>

        <div className="relative z-10 text-[11px] text-text-muted">
          © {new Date().getFullYear()}{' '}
          <a href="https://vitti.capital" target="_blank" rel="noopener noreferrer"
            className="text-text-secondary hover:text-blue-400 font-medium transition-colors">
            Vitti Capital
          </a>{' '}· Mirror Engine
        </div>
      </aside>

      {/* Right — form area */}
      <main className="relative flex-1 flex items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-[400px]">
          <div className="lg:hidden flex items-center gap-3 justify-center mb-7">
            <img src="/logo.jpg" alt="Mirror Engine" className="w-10 h-10 rounded-xl ring-1 ring-bg-border" />
            <span className="font-extrabold text-lg bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">
              Mirror Engine
            </span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

export function Field({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="group flex items-center gap-2.5 bg-bg-primary/70 border border-bg-border rounded-xl px-3.5 py-3 transition-all focus-within:border-blue-500/70 focus-within:ring-2 focus-within:ring-blue-500/15 focus-within:bg-bg-primary">
      <span className="text-text-muted group-focus-within:text-blue-400 shrink-0 transition-colors">{icon}</span>
      {children}
    </div>
  );
}

/** Password input with a show/hide eye toggle. */
export function PasswordField({
  value, onChange, placeholder = 'Password', required = true,
}: {
  value: string; onChange: (v: string) => void; placeholder?: string; required?: boolean;
}) {
  const [show, setShow] = useState(false);
  return (
    <Field icon={<Lock className="w-4 h-4" />}>
      <input
        type={show ? 'text' : 'password'}
        required={required}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted"
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        tabIndex={-1}
        aria-label={show ? 'Hide password' : 'Show password'}
        className="text-text-muted hover:text-text-secondary shrink-0 transition-colors"
      >
        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </Field>
  );
}

export function ErrorBanner({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <div className="flex items-start gap-2 text-[11.5px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2.5 animate-fade-in">
      <AlertCircle className="w-3.5 h-3.5 mt-px shrink-0 text-red-400" />
      <span className="leading-snug">{message}</span>
    </div>
  );
}

export function SubmitButton({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <button
      disabled={busy}
      className="group relative w-full flex items-center justify-center gap-2 py-3 rounded-xl bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white text-sm font-semibold shadow-lg shadow-blue-600/25 transition-all disabled:opacity-60 overflow-hidden"
    >
      <span className="pointer-events-none absolute top-0 left-0 h-full w-1/3 bg-gradient-to-r from-transparent via-white/25 to-transparent -translate-x-full group-hover:translate-x-[400%] transition-transform duration-700" />
      {children}
    </button>
  );
}
