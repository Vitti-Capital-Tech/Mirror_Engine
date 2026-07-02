'use client';
import React from 'react';

export function Loader({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="card-premium p-12 flex flex-col items-center justify-center gap-3">
      <div className="w-6 h-6 rounded-full border-2 border-blue-500/30 border-t-blue-500 animate-spin" />
      <span className="text-xs text-text-muted">{label}</span>
    </div>
  );
}
