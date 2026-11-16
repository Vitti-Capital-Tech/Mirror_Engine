'use client';
import React from 'react';
import { Eye, X } from 'lucide-react';

/**
 * "You are looking at someone else's desk."
 *
 * Picking a holder gives an admin exactly the view that holder gets — which is
 * the point, and also the hazard: the screen becomes indistinguishable from
 * being that person, and the next thing anyone does on it is add an account or
 * pause something. The header switcher does carry the name, but it is a
 * control in a row of controls and it reads as a filter.
 *
 * So the scoped state says so in one line, above the content, with the way out
 * next to it. Shown only to an admin, and only while the scope is narrowed —
 * a normal user is always looking at their own accounts and telling them so
 * would be telling them who they are.
 */
export function ViewingAs({ label, detail, status, onClear }: {
  label: string;
  detail?: string;
  status?: React.ReactNode;
  onClear: () => void;
}) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-blue-500/30 bg-blue-500/[0.07] px-3.5 py-2">
      <Eye className="w-3.5 h-3.5 text-blue-400 shrink-0" />
      <div className="min-w-0 flex-1 text-[12px] text-text-secondary truncate">
        Viewing as{' '}
        <span className="font-semibold text-text-primary">{label}</span>
        {detail ? <span className="text-text-muted"> · {detail}</span> : null}
      </div>
      {status ? <span className="shrink-0">{status}</span> : null}
      <button type="button" onClick={onClear}
        className="shrink-0 inline-flex items-center gap-1 rounded-lg border border-bg-border px-2 py-1 text-[11px] font-semibold text-text-secondary hover:text-text-primary hover:border-blue-500/50 transition-colors">
        <X className="w-3 h-3" /> All accounts
      </button>
    </div>
  );
}
