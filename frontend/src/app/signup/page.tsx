'use client';
import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { GoogleButton } from '@/components/auth/GoogleButton';
import { AuthShell, Field, SubmitButton } from '@/components/auth/AuthShell';
import { Mail, Lock, RefreshCw, ArrowRight, CheckCircle } from 'lucide-react';

export default function SignupPage() {
  const router = useRouter();
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
      setTimeout(() => router.replace('/login'), 1500);
    } catch (err: any) {
      setError(err.message || 'Signup failed');
    } finally { setBusy(false); }
  };

  return (
    <AuthShell>
      <div className="card-premium p-8">
        {done ? (
          <div className="text-center py-8">
            <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-emerald-500/10 ring-1 ring-emerald-500/20 mb-4 mx-auto glow-green">
              <CheckCircle className="w-7 h-7 text-emerald-400" />
            </div>
            <h1 className="text-lg font-bold text-text-primary tracking-tight">Account created</h1>
            <p className="text-xs text-text-muted mt-1.5">Redirecting to sign in…</p>
          </div>
        ) : (
          <>
            <h1 className="text-xl font-bold text-text-primary tracking-tight">Create your account</h1>
            <p className="text-xs text-text-muted mt-1.5 mb-6">Start mirroring trades in minutes.</p>
            <GoogleButton />
            <form onSubmit={submit} className="space-y-3">
              <Field icon={<Mail className="w-4 h-4" />}>
                <input type="email" required placeholder="Email" value={email} onChange={e => setEmail(e.target.value)}
                  className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
              </Field>
              <Field icon={<Lock className="w-4 h-4" />}>
                <input type="password" required placeholder="Password" value={password} onChange={e => setPassword(e.target.value)}
                  className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
              </Field>
              <Field icon={<Lock className="w-4 h-4" />}>
                <input type="password" required placeholder="Confirm password" value={confirm} onChange={e => setConfirm(e.target.value)}
                  className="w-full bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted" />
              </Field>
              {error && <p className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">{error}</p>}
              <SubmitButton busy={busy}>
                {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Create account <ArrowRight className="w-4 h-4" /></>}
              </SubmitButton>
            </form>
            <p className="text-xs text-text-muted mt-6 text-center">
              Already have an account? <Link href="/login" className="text-blue-400 font-semibold hover:text-blue-300 transition-colors">Sign in</Link>
            </p>
          </>
        )}
      </div>
    </AuthShell>
  );
}
