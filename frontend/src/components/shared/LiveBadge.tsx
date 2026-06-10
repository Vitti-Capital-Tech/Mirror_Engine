'use client';
import React from 'react';

export function LiveBadge({ connected }: { connected: boolean }) {
  return (
    <div className={`flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium border ${
      connected 
        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
        : 'bg-red-500/10 text-red-400 border-red-500/20'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${
        connected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'
      }`} />
      {connected ? 'LIVE' : 'OFFLINE'}
    </div>
  );
}
