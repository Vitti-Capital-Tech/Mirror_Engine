'use client';
import React from 'react';
import { ChevronDown, Check, Crown, CornerDownRight, Layers } from 'lucide-react';
import { useAccounts } from '@/hooks/useAccounts';
import { useAccountScope, groupByOwner, ALL_ACCOUNTS } from '@/context/AccountScopeContext';

/**
 * Pick which SET of accounts the dashboard is showing, or all of them.
 *
 * Signing in with `trade` lands on the shared admin account, and an admin is
 * exempt from scope_owned(), so /api/accounts hands back every account in the
 * system — including ones owned by someone else. Until now they all rendered
 * stacked on /positions with no way to single any of them out.
 *
 * The unit is the GROUP — a master together with the followers mirroring it —
 * not the individual account. Looking at one follower alone cannot answer the
 * only question this dashboard exists to answer ("is it matching its master?"),
 * so selecting a group keeps the pair together. Each group's members are listed
 * underneath its row as a reminder of what is in it, but they are labels, not
 * choices.
 *
 * This only filters the view. It is not a "log in as" — it changes nothing on
 * the server, touches no credentials, and cannot affect what the copy engine
 * does with any account.
 */
export function AccountSwitcher() {
  const { data: accounts = [] } = useAccounts();
  const { scope, setScope } = useAccountScope();
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  const groups = groupByOwner(accounts as any[]);

  // ALWAYS shown once there is anything to show. An earlier cut hid this
  // whenever there was only one group, reasoning there was nothing to switch
  // between — but that meant an admin signing in with `trade` saw no account
  // control at all and had no way to tell whose accounts were on screen. A
  // control that appears only once a second tenant exists cannot be found or
  // trusted. Option_Scope keeps its selector in the header permanently for the
  // same reason: it is the label as much as the switch.
  if (!accounts.length) return null;

  const selected = groups.find((g) => g.ownerId === scope);
  const label = selected ? selected.label : 'All accounts';

  const countLabel = (g: typeof groups[number]) => {
    const n = g.followers.length;
    return `${g.master ? 'master' : 'no master'} · ${n} follower${n === 1 ? '' : 's'}`;
  };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Choose which set of accounts the dashboard shows"
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-bg-border hover:border-blue-500/50 bg-bg-panel/40 transition-colors"
      >
        {selected
          ? <Crown className="w-3.5 h-3.5 text-amber-400 shrink-0" />
          : <Layers className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
        <span className="text-[11px] font-semibold text-text-primary max-w-[140px] truncate">{label}</span>
        <ChevronDown className={`w-3.5 h-3.5 text-text-muted transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute right-0 mt-1.5 w-64 max-h-[70vh] overflow-y-auto rounded-xl border border-bg-border bg-bg-panel shadow-2xl z-50 py-1 animate-fade-in">
          <button
            type="button"
            onClick={() => { setScope(ALL_ACCOUNTS); setOpen(false); }}
            className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-bg-secondary/70 transition-colors"
          >
            <Layers className="w-3.5 h-3.5 text-blue-400 shrink-0" />
            <span className="text-[12px] font-semibold text-text-primary flex-1">All accounts</span>
            {scope === ALL_ACCOUNTS && <Check className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
          </button>

          <div className="my-1 h-px bg-bg-border" />

          {groups.map((g) => (
            <button
              key={g.ownerId}
              type="button"
              onClick={() => { setScope(g.ownerId); setOpen(false); }}
              className="w-full px-3 py-2 text-left hover:bg-bg-secondary/70 transition-colors"
            >
              <span className="flex items-center gap-2">
                <Crown className="w-3.5 h-3.5 text-amber-400 shrink-0" />
                <span className="text-[12px] font-semibold text-text-primary truncate flex-1">{g.label}</span>
                {scope === g.ownerId && <Check className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
              </span>
              <span className="block pl-[22px] text-[10px] text-text-muted mt-0.5">{countLabel(g)}</span>
              {/* What is inside the group, so picking it is not a guess. */}
              {g.followers.map((f: any) => (
                <span key={f.id} className="flex items-center gap-1.5 pl-[22px] mt-1">
                  <CornerDownRight className="w-3 h-3 text-text-muted shrink-0" />
                  <span className="text-[11px] text-text-secondary truncate">{f.name}</span>
                  {f.status === 'paused' && (
                    <span className="text-[9px] font-bold tracking-wider text-text-muted border border-bg-border rounded px-1 py-px shrink-0">
                      PAUSED
                    </span>
                  )}
                </span>
              ))}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
