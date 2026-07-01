'use client';
import { createClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '';

export const googleEnabled = Boolean(url && anon);

// Browser Supabase client — used only for OAuth (e.g. Continue with Google).
// Email/password + 2FA still go through our own backend API.
// Guarded so a missing env at build time doesn't throw (createClient errors on
// empty url); the Google button simply hides when not configured.
export const supabase: ReturnType<typeof createClient> = googleEnabled
  ? createClient(url, anon, {
      auth: { persistSession: true, detectSessionInUrl: true, autoRefreshToken: true, flowType: 'pkce' },
    })
  : (null as any);
