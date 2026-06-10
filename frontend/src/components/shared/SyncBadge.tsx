'use client';
import React from 'react';

export function SyncBadge({ syncStatus }: { syncStatus: string }) {
  const normalized = syncStatus?.toLowerCase() || 'unknown';
  
  let styles = 'bg-slate-500/10 text-slate-400 border-slate-500/20';
  let label = 'UNKNOWN';

  if (normalized === 'synced') {
    styles = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
    label = 'SYNCED';
  } else if (normalized === 'out_of_sync') {
    styles = 'bg-red-500/10 text-red-400 border-red-500/20 animate-pulse border-red-500';
    label = 'MISMATCH';
  }

  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border ${styles}`}>
      {label}
    </span>
  );
}
