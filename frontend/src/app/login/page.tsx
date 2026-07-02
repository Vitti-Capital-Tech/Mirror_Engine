'use client';
import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { GoogleButton } from '@/components/auth/GoogleButton';
import { Mail, Lock, ShieldCheck, RefreshCw, ArrowRight } from 'lucide-react';

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
        // 2FA disabled — logged in directly
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
    <div className="min-h-screen w-screen flex items-center justify-center bg-bg-primary p-4">
      <div className="w-full max-w-[400px]">
        <div className="flex items-center gap-3 justify-center mb-6">
          <img src="/logo.jpg" alt="Mirror Engine" className="w-10 h-10 rounded-xl ring-1 ring-bg-border" />
          <span className="font-extrabold text-lg bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">Mirror Engine</span>
        </div>

        <div className="card-premium p-7">
          {step === 'creds' ? (
            <>
              <h1 className="text-base font-bold text-text-primary">Sign in</h1>
              <p className="text-xs text-text-muted mt-1 mb-5">Enter your credentials to continue.</p>
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
                {error && <p className="text-[11px] text-red-400">{error}</p>}
                <button disabled={busy} className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-all disabled:opacity-60">
                  {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Continue <ArrowRight className="w-4 h-4" /></>}
                </button>
              </form>
              <p className="text-[11px] text-text-muted mt-5 text-center">
                No account? <Link href="/signup" className="text-blue-400 font-semibold">Create one</Link>
              </p>
            </>
          ) : (
            <>
              <div className="flex items-center justify-center w-11 h-11 rounded-2xl bg-blue-500/10 mb-3 mx-auto">
                <ShieldCheck className="w-5 h-5 text-blue-400" />
              </div>
              <h1 className="text-base font-bold text-text-primary text-center">Enter verification code</h1>
              <p className="text-xs text-text-muted mt-1 mb-5 text-center">We emailed a 6-digit code to <span className="text-text-secondary font-medium">{email}</span>.</p>
              <form onSubmit={submitOtp} className="space-y-3">
                <input inputMode="numeric" maxLength={6} required placeholder="••••••" value={code}
                  onChange={e => setCode(e.target.value.replace(/\D/g, ''))}
                  className="w-full bg-bg-primary border border-bg-border rounded-xl px-3 py-3 text-center text-2xl font-bold tracking-[0.4em] font-mono text-text-primary outline-none focus:border-blue-500" />
                {error && <p className="text-[11px] text-red-400">{error}</p>}
                <button disabled={busy || code.length < 6} className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-all disabled:opacity-60">
                  {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : 'Verify & sign in'}
                </button>
              </form>
              <div className="flex items-center justify-between mt-4 text-[11px]">
                <button onClick={() => { setStep('creds'); setCode(''); setError(''); }} className="text-text-muted hover:text-text-secondary">← Back</button>
                <button onClick={resend} disabled={busy} className="text-blue-400 font-semibold disabled:opacity-50">Resend code</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Field({ icon, children }: { icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 bg-bg-primary border border-bg-border rounded-xl px-3 py-2.5 focus-within:border-blue-500 transition-colors">
      <span className="text-text-muted shrink-0">{icon}</span>
      {children}
    </div>
  );
}
