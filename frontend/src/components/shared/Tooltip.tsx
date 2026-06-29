'use client';
import React from 'react';

export function Tooltip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="relative group/tt inline-flex">
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 mb-2 -translate-x-1/2 translate-y-1 whitespace-nowrap rounded-md border border-bg-border bg-bg-elevated px-2 py-1 text-[10px] font-semibold text-text-primary opacity-0 shadow-xl shadow-black/40 transition-all duration-150 group-hover/tt:opacity-100 group-hover/tt:translate-y-0 z-50"
      >
        {label}
        <span className="absolute top-full left-1/2 -translate-x-1/2 -mt-px border-4 border-transparent border-t-bg-elevated" />
      </span>
    </span>
  );
}
