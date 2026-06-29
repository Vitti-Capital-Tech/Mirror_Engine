'use client';
import React from 'react';

export function StatusBadge({ status }: { status: string }) {
  const normalized = status?.toLowerCase() || 'paused';

  let dot = 'bg-slate-400';
  let styles = 'bg-slate-500/10 text-slate-300 border-slate-500/20';
  let label = 'Paused';
  let pulse = false;

  if (normalized === 'active') {
    dot = 'bg-emerald-400';
    styles = 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20';
    label = 'Active';
    pulse = true;
  } else if (normalized === 'paused') {
    dot = 'bg-slate-400';
    styles = 'bg-slate-500/10 text-slate-300 border-slate-500/20';
    label = 'Paused';
  } else if (normalized === 'error') {
    dot = 'bg-rose-400';
    styles = 'bg-rose-500/10 text-rose-300 border-rose-500/20';
    label = 'Error';
  }

  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border ${styles}`}>
      <span className="relative flex h-1.5 w-1.5">
        {pulse && <span className={`absolute inline-flex h-full w-full rounded-full ${dot} opacity-60 animate-ping`} />}
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${dot}`} />
      </span>
      {label}
    </span>
  );
}
