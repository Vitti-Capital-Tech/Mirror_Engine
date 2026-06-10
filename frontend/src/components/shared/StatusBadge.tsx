'use client';
import React from 'react';

export function StatusBadge({ status }: { status: string }) {
  const normalized = status?.toLowerCase() || 'paused';
  
  let styles = 'bg-slate-500/10 text-slate-400 border-slate-500/20';
  let label = status;

  if (normalized === 'active') {
    styles = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
    label = 'ACTIVE';
  } else if (normalized === 'paused') {
    styles = 'bg-amber-500/10 text-amber-400 border-amber-500/20';
    label = 'PAUSED';
  } else if (normalized === 'error') {
    styles = 'bg-orange-500/10 text-orange-400 border-orange-500/20';
    label = 'ERROR';
  } else if (normalized === 'circuit_break') {
    styles = 'bg-red-500/10 text-red-400 border-red-500/20 animate-pulse';
    label = 'BLOCKED (CB)';
  }

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border ${styles}`}>
      {label}
    </span>
  );
}
