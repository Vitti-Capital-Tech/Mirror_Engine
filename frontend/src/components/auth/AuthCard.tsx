'use client';
import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { Field, SubmitButton, ErrorBanner } from '@/components/auth/AuthShell';
import { Mail, ShieldCheck, RefreshCw, ArrowRight, ArrowLeft } from 'lucide-react';

/** The login card. There is no signup: typing an email that has never been
 *  seen signs you in and creates the account on the spot, so a separate
 *  "create account" form had nothing left to do. Option_Scope, which this login
 *  was modelled on, has no signup route either. */
export function AuthCard() {
  const router = useRouter();
  const { setSession } = useAuth();
  return <LoginForm setSession={setSession} router={router} active />;
}

async function finishLogin(router: any, setSession: any, token: string, refreshToken?: string) {
  // Store the token, then resolve the FULL user (incl. role) before navigating,
  // so the correct panel (admin vs trader) shows immediately — no refresh needed.
  // The refresh token must be stored too: the access token expires in an hour,
  // and without it the only way back in was a fresh login.
  setSession(token, undefined, refreshToken);
  let me: any = null;
  try { me = await api.auth.me(); } catch { /* ignore */ }
  if (me) setSession(token, { id: me.id, email: me.email, role: me.role }, refreshToken);
  router.replace(me?.role === 'admin' ? '/admin' : '/positions');
}

function LoginForm({ setSession, router, active }: any) {
  const [step, setStep] = useState<'creds' | 'otp'>('creds');
  const [email, setEmail] = useState('');
  const [pendingId, setPendingId] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submitCreds = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      // No password field on this form at all: the email is the whole login.
      // The backend treats a blank password as "passwordless" and signs the
      // user in, creating the account the first time it sees the address.
      const res = await api.auth.login(email, '');
      if (res.twofa_required) { setPendingId(res.pending_id); setStep('otp'); }
      else { await finishLogin(router, setSession, res.access_token, res.refresh_token); }
    } catch (err: any) {
      const msg = err.message || 'Login failed';
      setError(/invalid email or password/i.test(msg)
        ? "We couldn't sign you in — check the email address and try again."
        : msg);
    } finally { setBusy(false); }
  };

  const submitOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const res = await api.auth.verify2fa(pendingId, code.trim());
      await finishLogin(router, setSession, res.access_token, res.refresh_token);
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
          <p className="text-xs text-text-muted mt-1.5 mb-6">Enter your email to sign in.</p>
          <form onSubmit={submitCreds} className="space-y-3">
            <Field icon={<Mail className="w-4 h-4" />}>
              <input type="text" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} tabIndex={active ? 0 : -1}
                autoComplete="email" autoFocus={active}
                className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
            </Field>
            <ErrorBanner message={error} />
            <SubmitButton busy={busy}>
              {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Sign in <ArrowRight className="w-4 h-4" /></>}
            </SubmitButton>
          </form>
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
