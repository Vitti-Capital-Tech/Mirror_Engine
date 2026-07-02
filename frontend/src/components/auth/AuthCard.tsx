'use client';
import React, { useLayoutEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { GoogleButton } from '@/components/auth/GoogleButton';
import { Field, PasswordField, SubmitButton, ErrorBanner } from '@/components/auth/AuthShell';
import { Mail, ShieldCheck, RefreshCw, ArrowRight, ArrowLeft, CheckCircle } from 'lucide-react';

type Mode = 'login' | 'signup';

/** Flip-card auth: login on the front, signup on the back. Clicking "Create one"
 *  rolls to signup; signing up (or "Sign in") rolls back to login. */
export function AuthCard({ initialMode = 'login' }: { initialMode?: Mode }) {
  const router = useRouter();
  const { setSession } = useAuth();
  const [mode, setMode] = useState<Mode>(initialMode);

  const frontRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLDivElement>(null);
  const [h, setH] = useState<number | undefined>(undefined);

  // Keep the flip container height matched to whichever face is showing,
  // and re-measure whenever that face grows/shrinks (errors, step change).
  useLayoutEffect(() => {
    const el = (mode === 'login' ? frontRef : backRef).current;
    if (!el) return;
    const measure = () => setH(el.offsetHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [mode]);

  const goSignup = () => { setMode('signup'); window.history.replaceState(null, '', '/signup'); };
  const goLogin = () => { setMode('login'); window.history.replaceState(null, '', '/login'); };

  return (
    <div style={{ perspective: '1600px' }}>
      <div
        className="relative"
        style={{
          transformStyle: 'preserve-3d',
          transition: 'transform 0.7s cubic-bezier(0.4, 0, 0.2, 1), height 0.5s cubic-bezier(0.4, 0, 0.2, 1)',
          transform: mode === 'signup' ? 'rotateY(-180deg)' : 'rotateY(0deg)',
          height: h,
        }}
      >
        {/* Front — Login */}
        <div ref={frontRef} style={{ backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden' }} className="w-full">
          <LoginForm onSwitch={goSignup} setSession={setSession} router={router} active={mode === 'login'} />
        </div>
        {/* Back — Signup */}
        <div
          ref={backRef}
          style={{ backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden', transform: 'rotateY(180deg)', position: 'absolute', inset: 0 }}
          className="w-full"
        >
          <SignupForm onSwitch={goLogin} onDone={goLogin} active={mode === 'signup'} />
        </div>
      </div>
    </div>
  );
}

async function redirectAfterLogin(router: any, fallbackUser?: any) {
  // Send admins to the admin panel, everyone else to positions.
  let role = fallbackUser?.role;
  if (!role) {
    try { role = (await api.auth.me())?.role; } catch { /* ignore */ }
  }
  router.replace(role === 'admin' ? '/admin' : '/positions');
}

function LoginForm({ onSwitch, setSession, router, active }: any) {
  const [step, setStep] = useState<'creds' | 'otp'>('creds');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [pendingId, setPendingId] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submitCreds = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const res = await api.auth.login(email, password);
      if (res.twofa_required) { setPendingId(res.pending_id); setStep('otp'); }
      else { setSession(res.access_token, res.user ? { id: res.user.id, email: res.user.email } : undefined); await redirectAfterLogin(router); }
    } catch (err: any) {
      const msg = err.message || 'Login failed';
      setError(/invalid email or password/i.test(msg)
        ? "We couldn't sign you in — check your email and password, or create an account if you don't have one yet."
        : msg);
    } finally { setBusy(false); }
  };

  const submitOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const res = await api.auth.verify2fa(pendingId, code.trim());
      setSession(res.access_token, res.user ? { id: res.user.id, email: res.user.email } : undefined);
      await redirectAfterLogin(router);
    } catch (err: any) { setError(err.message || 'Verification failed'); } finally { setBusy(false); }
  };

  const resend = async () => {
    setError(''); setBusy(true);
    try { await api.auth.resend2fa(pendingId); } catch (err: any) { setError(err.message || 'Could not resend'); } finally { setBusy(false); }
  };

  return (
    <div className="card-premium p-8" aria-hidden={!active}>
      {step === 'creds' ? (
        <>
          <h1 className="text-xl font-bold text-text-primary tracking-tight">Welcome back</h1>
          <p className="text-xs text-text-muted mt-1.5 mb-6">Sign in to your Mirror Engine account.</p>
          <GoogleButton />
          <form onSubmit={submitCreds} className="space-y-3">
            <Field icon={<Mail className="w-4 h-4" />}>
              <input type="text" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} tabIndex={active ? 0 : -1}
                className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
            </Field>
            <PasswordField value={password} onChange={setPassword} />
            <ErrorBanner message={error} />
            <SubmitButton busy={busy}>
              {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Sign in <ArrowRight className="w-4 h-4" /></>}
            </SubmitButton>
          </form>
          <p className="text-xs text-text-muted mt-6 text-center">
            No account?{' '}
            <button type="button" onClick={onSwitch} tabIndex={active ? 0 : -1} className="text-blue-400 font-semibold hover:text-blue-300 transition-colors">Create one</button>
          </p>
        </>
      ) : (
        <>
          <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-blue-500/10 ring-1 ring-blue-500/20 mb-4 mx-auto">
            <ShieldCheck className="w-5 h-5 text-blue-400" />
          </div>
          <h1 className="text-lg font-bold text-text-primary text-center tracking-tight">Enter verification code</h1>
          <p className="text-xs text-text-muted mt-1.5 mb-6 text-center">We emailed a 6-digit code to <span className="text-text-secondary font-medium">{email}</span>.</p>
          <form onSubmit={submitOtp} className="space-y-3">
            <input inputMode="numeric" maxLength={6} required placeholder="••••••" value={code}
              onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
              className="w-full bg-bg-primary border border-bg-border rounded-xl px-3 py-3.5 text-center text-2xl font-bold tracking-[0.4em] font-mono text-text-primary outline-none focus:border-blue-500/70 focus:ring-2 focus:ring-blue-500/15 transition-all" />
            <ErrorBanner message={error} />
            <SubmitButton busy={busy || code.length < 6}>
              {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : 'Verify & sign in'}
            </SubmitButton>
          </form>
          <div className="flex items-center justify-between mt-5 text-[11px]">
            <button onClick={() => { setStep('creds'); setCode(''); setError(''); }} className="flex items-center gap-1 text-text-muted hover:text-text-secondary transition-colors">
              <ArrowLeft className="w-3 h-3" /> Back
            </button>
            <button onClick={resend} disabled={busy} className="text-blue-400 font-semibold hover:text-blue-300 disabled:opacity-50 transition-colors">Resend code</button>
          </div>
        </>
      )}
    </div>
  );
}

function SignupForm({ onSwitch, onDone, active }: any) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    if (password !== confirm) { setError('Passwords do not match.'); return; }
    setBusy(true);
    try {
      await api.auth.signup(email, password);
      setDone(true);
      setTimeout(() => { onDone(); setTimeout(() => setDone(false), 700); }, 1300);
    } catch (err: any) { setError(err.message || 'Signup failed'); } finally { setBusy(false); }
  };

  return (
    <div className="card-premium p-8" aria-hidden={!active}>
      {done ? (
        <div className="text-center py-8">
          <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-emerald-500/10 ring-1 ring-emerald-500/20 mb-4 mx-auto glow-green">
            <CheckCircle className="w-7 h-7 text-emerald-400" />
          </div>
          <h1 className="text-lg font-bold text-text-primary tracking-tight">Account created</h1>
          <p className="text-xs text-text-muted mt-1.5">Taking you to sign in…</p>
        </div>
      ) : (
        <>
          <h1 className="text-xl font-bold text-text-primary tracking-tight">Create your account</h1>
          <p className="text-xs text-text-muted mt-1.5 mb-6">Start mirroring trades in minutes.</p>
          <GoogleButton />
          <form onSubmit={submit} className="space-y-3">
            <Field icon={<Mail className="w-4 h-4" />}>
              <input type="email" required={active} placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} tabIndex={active ? 0 : -1}
                className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
            </Field>
            <PasswordField value={password} onChange={setPassword} required={active} />
            <PasswordField value={confirm} onChange={setConfirm} placeholder="Confirm password" required={active} />
            <ErrorBanner message={error} />
            <SubmitButton busy={busy}>
              {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Create account <ArrowRight className="w-4 h-4" /></>}
            </SubmitButton>
          </form>
          <p className="text-xs text-text-muted mt-6 text-center">
            Already have an account?{' '}
            <button type="button" onClick={onSwitch} tabIndex={active ? 0 : -1} className="text-blue-400 font-semibold hover:text-blue-300 transition-colors">Sign in</button>
          </p>
        </>
      )}
    </div>
  );
}
