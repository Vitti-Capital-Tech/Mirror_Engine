'use client';
import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { GoogleButton } from '@/components/auth/GoogleButton';
import { AuthShell, Field, SubmitButton } from '@/components/auth/AuthShell';
import { Mail, Lock, ShieldCheck, RefreshCw, ArrowRight, ArrowLeft } from 'lucide-react';

export default function LoginPage() {
  const router = useRouter();
  const { setSession } = useAuth();

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
      if (res.twofa_required) {
        setPendingId(res.pending_id);
        setStep('otp');
      } else {
        setSession(res.access_token, res.user ? { id: res.user.id, email: res.user.email } : undefined);
        router.replace('/positions');
      }
    } catch (err: any) {
      setError(err.message || 'Login failed');
    } finally { setBusy(false); }
  };

  const submitOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(''); setBusy(true);
    try {
      const res = await api.auth.verify2fa(pendingId, code.trim());
      setSession(res.access_token, res.user ? { id: res.user.id, email: res.user.email } : undefined);
      router.replace('/positions');
    } catch (err: any) {
      setError(err.message || 'Verification failed');
    } finally { setBusy(false); }
  };

  const resend = async () => {
    setError(''); setBusy(true);
    try { await api.auth.resend2fa(pendingId); } catch (err: any) { setError(err.message || 'Could not resend'); }
    finally { setBusy(false); }
  };

  return (
    <AuthShell>
      <div className="card-premium p-8">
        {step === 'creds' ? (
          <>
            <h1 className="text-xl font-bold text-text-primary tracking-tight">Welcome back</h1>
            <p className="text-xs text-text-muted mt-1.5 mb-6">Sign in to your Mirror Engine account.</p>
            <GoogleButton />
            <form onSubmit={submitCreds} className="space-y-3">
              <Field icon={<Mail className="w-4 h-4" />}>
                <input type="text" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)}
                  className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
              </Field>
              <Field icon={<Lock className="w-4 h-4" />}>
                <input type="password" required placeholder="Password" value={password} onChange={e => setPassword(e.target.value)}
                  className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
              </Field>
              {error && <p className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</p>}
              <SubmitButton busy={busy}>
                {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Sign in <ArrowRight className="w-4 h-4" /></>}
              </SubmitButton>
            </form>
            <p className="text-xs text-text-muted mt-6 text-center">
              No account? <Link href="/signup" className="text-blue-400 font-semibold hover:text-blue-300 transition-colors">Create one</Link>
            </p>
          </>
        ) : (
          <>
            <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-blue-500/10 ring-1 ring-blue-500/20 mb-4 mx-auto">
              <ShieldCheck className="w-5 h-5 text-blue-400" />
            </div>
            <h1 className="text-lg font-bold text-text-primary text-center tracking-tight">Enter verification code</h1>
            <p className="text-xs text-text-muted mt-1.5 mb-6 text-center">
              We emailed a 6-digit code to <span className="text-text-secondary font-medium">{email}</span>.
            </p>
            <form onSubmit={submitOtp} className="space-y-3">
              <input inputMode="numeric" maxLength={6} required placeholder="••••••" value={code}
                onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                className="w-full bg-bg-primary border border-bg-border rounded-xl px-3 py-3.5 text-center text-2xl font-bold tracking-[0.4em] font-mono text-text-primary outline-none focus:border-blue-500/70 focus:ring-2 focus:ring-blue-500/15 transition-all" />
              {error && <p className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</p>}
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
    </AuthShell>
  );
}
