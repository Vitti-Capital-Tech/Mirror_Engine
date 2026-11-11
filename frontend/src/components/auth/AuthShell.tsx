'use client';
import React, { useState } from 'react';
import { Eye, EyeOff, Lock, AlertCircle } from 'lucide-react';

/** The auth page: one centred column.
 *
 *  This used to be a split screen — a 46% brand/feature panel on the left, the
 *  form on the right — fed by a separate marketing landing page at "/". With
 *  email-only sign-in there is nothing to sell and nothing to choose: the whole
 *  product is one input. Both the panel and the landing page are gone, "/"
 *  redirects here, and the card sits in the middle of the screen.
 *
 *  The brand lockup and ambient orbs are kept so it still looks like something,
 *  just centred rather than off to one side. */
export function AuthShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative min-h-screen w-screen flex items-center justify-center bg-bg-primary overflow-hidden px-6 py-10">
      {/* Drifting ambient orbs */}
      <div className="orb-a pointer-events-none absolute -top-32 -left-24 w-[28rem] h-[28rem] rounded-full blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(59,130,246,0.20), transparent 65%)' }} />
      <div className="orb-b pointer-events-none absolute -bottom-32 -right-24 w-[30rem] h-[30rem] rounded-full blur-3xl"
        style={{ background: 'radial-gradient(circle, rgba(139,92,246,0.16), transparent 65%)' }} />
      {/* Faint grid, faded out from the centre */}
      <div className="pointer-events-none absolute inset-0 opacity-[0.15]"
        style={{
          backgroundImage:
            'linear-gradient(to right, rgba(148,163,184,0.12) 1px, transparent 1px),' +
            'linear-gradient(to bottom, rgba(148,163,184,0.12) 1px, transparent 1px)',
          backgroundSize: '44px 44px',
          maskImage: 'radial-gradient(circle at 50% 50%, black, transparent 70%)',
          WebkitMaskImage: 'radial-gradient(circle at 50% 50%, black, transparent 70%)',
        }} />

      <div className="relative z-10 w-full max-w-[400px]">
        <div className="flex items-center justify-center gap-3 mb-7 hero-rise hero-d1">
          <div className="relative w-11 h-11 rounded-2xl overflow-hidden ring-1 ring-bg-border shadow-lg shadow-blue-500/20">
            <img src="/logo.jpg" alt="Mirror Engine" className="w-full h-full object-cover" />
            <span className="pointer-events-none absolute top-0 left-0 h-full w-2/3 bg-gradient-to-r from-transparent via-white/55 to-transparent animate-shimmer" />
          </div>
          <span className="font-extrabold text-xl bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">
            Mirror Engine
          </span>
        </div>

        <div className="hero-rise hero-d2">{children}</div>

        <p className="text-center text-[11px] text-text-muted mt-6 hero-rise hero-d3">
          © {new Date().getFullYear()}{' '}
          <a href="https://vitti.capital" target="_blank" rel="noopener noreferrer"
            className="text-text-secondary hover:text-blue-400 font-medium transition-colors">
            Vitti Capital
          </a>{' '}· Mirror Engine
        </p>
      </div>
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
