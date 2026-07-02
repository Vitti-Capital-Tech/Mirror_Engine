'use client';
import React from 'react';
import { AuthShell } from '@/components/auth/AuthShell';
import { AuthCard } from '@/components/auth/AuthCard';

export default function LoginPage() {
  return (
    <AuthShell>
      <AuthCard initialMode="login" />
    </AuthShell>
  );
}
