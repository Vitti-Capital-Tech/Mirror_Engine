'use client';
import React, { useState } from 'react';
import Link from 'next/link';
import { Radio, Scale, ShieldCheck, TrendingUp, Eye, EyeOff, Lock, AlertCircle, ArrowLeft } from 'lucide-react';

const FEATURES = [
  { icon: Radio, anim: 'icon-pulse', title: 'Real-time mirroring', desc: 'Master fills copy to every follower in milliseconds.' },
  { icon: Scale, anim: 'icon-wobble', title: 'Balance-ratio sizing', desc: 'Each account sized precisely to its own capital.' },
  { icon: ShieldCheck, anim: 'icon-float', title: 'Encrypted & isolated', desc: 'Keys encrypted at rest, per-user data isolation.' },
];

/** Static list of all feature highlights. */
function FeatureList() {
  return (
    <div className="mt-8 space-y-3.5">
      {FEATURES.map((f) => {
        const Icon = f.icon;
        return (
          <div key={f.title} className="flex items-start gap-3.5">
            <div className="mt-0.5 flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500/20 to-blue-500/5 ring-1 ring-blue-500/25 shrink-0 shadow-lg shadow-blue-500/10">
              <Icon className={`w-[18px] h-[18px] text-blue-300 ${f.anim}`} strokeWidth={2} />
            </div>
            <div>
              <div className="text-sm font-semibold text-text-primary">{f.title}</div>
              <div className="text-xs text-text-muted mt-0.5 leading-relaxed max-w-xs">{f.desc}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Animated copy-trading graphic: master node broadcasting trades to followers. */
export function MirrorGraphic() {
  const lines = [
    'M160 46 L56 156',
    'M160 46 L160 156',
    'M160 46 L264 156',
  ];
  const followers = [56, 160, 264];
  return (
    <svg viewBox="0 0 320 190" className="w-full h-auto max-w-[380px]" fill="none">
      <defs>
        <radialGradient id="masterG" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#93c5fd" />
          <stop offset="100%" stopColor="#3b82f6" />
        </radialGradient>
        <radialGradient id="followerG" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#6ee7b7" />
          <stop offset="100%" stopColor="#10b981" />
        </radialGradient>
        <radialGradient id="dotG" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#e0f2fe" />
          <stop offset="100%" stopColor="#38bdf8" />
        </radialGradient>
      </defs>

      {/* Connector lines + flowing trade dots */}
      {lines.map((d, i) => (
        <g key={i}>
          <path d={d} stroke="#3b82f6" strokeWidth="1" opacity="0.18" />
          <path d={d} stroke="#60a5fa" strokeWidth="1.5" className="dash-flow" opacity="0.6" />
          <circle r="3.4" fill="url(#dotG)">
            <animateMotion dur="2.4s" repeatCount="indefinite" path={d} begin={`${i * 0.8}s`} />
            <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.12;0.85;1" dur="2.4s" repeatCount="indefinite" begin={`${i * 0.8}s`} />
          </circle>
        </g>
      ))}

      {/* Follower nodes */}
      {followers.map((x, i) => (
        <g key={i}>
          <circle cx={x} cy="156" r="9" fill="url(#followerG)" className="node-breathe" style={{ animationDelay: `${i * 0.4}s` }} />
          <circle cx={x} cy="156" r="9" fill="none" stroke="#10b981" strokeWidth="1" opacity="0.35" />
        </g>
      ))}

      {/* Master node with pulsing rings + crown */}
      <circle cx="160" cy="46" r="13" fill="none" stroke="#3b82f6" strokeWidth="1.5" className="pulse-ring" />
      <circle cx="160" cy="46" r="13" fill="none" stroke="#3b82f6" strokeWidth="1.5" className="pulse-ring pulse-ring-2" />
      <circle cx="160" cy="46" r="13" fill="url(#masterG)" className="node-breathe" />
      <path d="M154 47 l2.5 -4 l3.5 3 l3.5 -3 l2.5 4 z" fill="#fde68a" stroke="#f59e0b" strokeWidth="0.6" strokeLinejoin="round" />

      {/* Labels */}
      <text x="160" y="20" textAnchor="middle" fontSize="8" fontWeight="700" letterSpacing="1.5" fill="#93a1b8">MASTER</text>
      <text x="160" y="182" textAnchor="middle" fontSize="8" fontWeight="700" letterSpacing="1.5" fill="#93a1b8">FOLLOWERS</text>
    </svg>
  );
}

/** Premium split-screen wrapper for the auth pages. */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen w-screen flex bg-bg-primary overflow-hidden">
      {/* Left — brand / hero (hidden on small screens) */}
      <aside className="relative hidden lg:flex flex-col justify-between w-[46%] max-w-[620px] p-12 xl:p-14 overflow-hidden border-r border-bg-border">
        {/* Drifting ambient orbs */}
        <div className="orb-a pointer-events-none absolute -top-24 -left-16 w-80 h-80 rounded-full blur-3xl"
          style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.22), transparent 65%)' }} />
        <div className="orb-b pointer-events-none absolute bottom-0 -right-10 w-96 h-96 rounded-full blur-3xl"
          style={{ background: 'radial-gradient(circle, rgba(139,92,246,0.18), transparent 65%)' }} />
        {/* Faint grid */}
        <div className="pointer-events-none absolute inset-0 opacity-[0.15]"
          style={{
            backgroundImage:
              'linear-gradient(to right, rgba(148,163,184,0.12) 1px, transparent 1px),' +
              'linear-gradient(to bottom, rgba(148,163,184,0.12) 1px, transparent 1px)',
            backgroundSize: '44px 44px',
            maskImage: 'radial-gradient(circle at 30% 30%, black, transparent 75%)',
          }} />

        <div className="relative z-10 flex items-center gap-3 hero-rise hero-d1">
          <div className="relative w-11 h-11 rounded-2xl overflow-hidden ring-1 ring-bg-border shadow-lg shadow-blue-500/20">
            <img src="/logo.jpg" alt="Mirror Engine" className="w-full h-full object-cover" />
            <span className="pointer-events-none absolute top-0 left-0 h-full w-2/3 bg-gradient-to-r from-transparent via-white/55 to-transparent animate-shimmer" />
          </div>
          <span className="font-extrabold text-xl bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">
            Mirror Engine
          </span>
        </div>

        <div className="relative z-10 max-w-md">
          {/* Shimmering badge */}
          <div className="inline-flex items-center gap-1.5 rounded-full border border-bg-border bg-bg-panel/50 px-3 py-1 text-[11px] font-semibold text-text-secondary mb-6 hero-rise hero-d2">
            <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> Copy-trading for Delta Exchange
          </div>
          <h1 className="text-3xl xl:text-4xl font-extrabold text-text-primary leading-tight tracking-tight hero-rise hero-d3">
            One master.<br />
            <span className="bg-gradient-to-r from-sky-300 to-blue-500 bg-clip-text text-transparent">Infinite mirrors.</span>
          </h1>

          {/* Live animated graphic */}
          <div className="hero-rise hero-d4 mt-6">
            <MirrorGraphic />
          </div>

          <div className="hero-rise hero-d5">
            <FeatureList />
          </div>
        </div>

        <div className="relative z-10 text-[11px] text-text-muted hero-rise hero-d5">
          © {new Date().getFullYear()}{' '}
          <a href="https://vitti.capital" target="_blank" rel="noopener noreferrer"
            className="text-text-secondary hover:text-blue-400 font-medium transition-colors">
            Vitti Capital
          </a>{' '}· Mirror Engine
        </div>
      </aside>

      {/* Right — form area */}
      <main className="relative flex-1 flex items-center justify-center p-6 sm:p-10">
        {/* Back to landing */}
        <Link href="/"
          className="absolute top-6 left-6 sm:top-8 sm:left-8 inline-flex items-center gap-1.5 text-xs font-semibold text-text-muted hover:text-text-primary transition-colors">
          <ArrowLeft className="w-4 h-4" /> Back
        </Link>
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
      {value.length > 0 && (
        <button
          type="button"
          onClick={() => setShow((s) => !s)}
          tabIndex={-1}
          aria-label={show ? 'Hide password' : 'Show password'}
          className="text-text-muted hover:text-text-secondary shrink-0 transition-colors"
        >
          {show ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
        </button>
      )}
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
