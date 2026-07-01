'use client';
import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { api } from '@/lib/api';
import { GoogleButton } from '@/components/auth/GoogleButton';
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
    <div className="min-h-screen w-screen flex items-center justify-center bg-bg-primary p-4">
      <div className="w-full max-w-[400px]">
        <div className="flex items-center gap-3 justify-center mb-6">
          <img src="/logo.jpg" alt="Mirror Engine" className="w-10 h-10 rounded-xl ring-1 ring-bg-border" />
          <span className="font-extrabold text-lg bg-gradient-to-r from-white via-sky-200 to-blue-400 bg-clip-text text-transparent tracking-tight">Mirror Engine</span>
        </div>

        <div className="card-premium p-7">
          {done ? (
            <div className="text-center py-6">
              <div className="flex items-center justify-center w-12 h-12 rounded-2xl bg-emerald-500/10 mb-3 mx-auto">
                <CheckCircle className="w-6 h-6 text-emerald-400" />
              </div>
              <h1 className="text-base font-bold text-text-primary">Account created</h1>
              <p className="text-xs text-text-muted mt-1">Redirecting to sign in…</p>
            </div>
          ) : (
            <>
              <h1 className="text-base font-bold text-text-primary">Create account</h1>
              <p className="text-xs text-text-muted mt-1 mb-5">Set up your Mirror Engine login.</p>
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
                {error && <p className="text-[11px] text-red-400">{error}</p>}
                <button disabled={busy} className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold transition-all disabled:opacity-60">
                  {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <>Create account <ArrowRight className="w-4 h-4" /></>}
                </button>
              </form>
              <p className="text-[11px] text-text-muted mt-5 text-center">
                Already have an account? <Link href="/login" className="text-blue-400 font-semibold">Sign in</Link>
              </p>
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
