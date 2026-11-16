'use client';
import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';

export interface SelectOption {
  value: string;
  label: string;
  /** Shown, but cannot be picked — e.g. a master whose copy slots are all
   *  used. Listing it faded explains why it is not an option; leaving it out
   *  entirely just looks like the account is missing. */
  disabled?: boolean;
  /** A short word in the trailing slot, e.g. "Full". Carries the reason a row
   *  is unavailable, so the fade is not the only thing saying so. */
  badge?: string;
}

interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  className?: string;
  placeholder?: string;
  size?: 'sm' | 'md' | 'lg';
}

export function Select({ value, onChange, options, className = '', placeholder = 'Select…', size = 'md' }: SelectProps) {
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const ref = useRef<HTMLDivElement>(null);

  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  useEffect(() => {
    if (open) {
      const idx = options.findIndex((o) => o.value === value && !o.disabled);
      setHighlight(idx >= 0 ? idx : options.findIndex((o) => !o.disabled));
    }
  }, [open, value, options]);

  const pad = size === 'sm' ? 'px-2.5 py-1.5 text-xs'
    : size === 'lg' ? 'px-3 py-2.5 text-[13px]'
    : 'px-3 py-2 text-xs';

  const choose = (v: string) => {
    if (options.find((o) => o.value === v)?.disabled) return;
    onChange(v);
    setOpen(false);
  };

  /** Next selectable index in `dir`, skipping disabled rows; stays put if
   *  there is nothing selectable that way. */
  const step = (from: number, dir: 1 | -1) => {
    for (let i = from + dir; i >= 0 && i < options.length; i += dir) {
      if (!options[i].disabled) return i;
    }
    return from;
  };

  return (
    <div className={`relative ${className}`} ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setHighlight((h) => step(h, 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight((h) => step(h, -1)); }
          else if (e.key === 'Enter' && open) { e.preventDefault(); choose(options[highlight].value); }
          else if (e.key === 'Escape') setOpen(false);
        }}
        className={`w-full flex items-center justify-between gap-2 bg-bg-primary border rounded-lg text-text-primary outline-none transition-all cursor-pointer ${pad} ${
          open ? 'border-blue-500 ring-2 ring-blue-500/20' : 'border-bg-border hover:border-bg-border'
        }`}
      >
        <span className={`truncate ${selected ? '' : 'text-text-muted'}`}>{selected ? selected.label : placeholder}</span>
        <ChevronDown className={`w-3.5 h-3.5 text-text-muted shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute z-50 mt-1.5 w-full rounded-xl border border-bg-border bg-bg-elevated shadow-2xl shadow-black/50 p-1 animate-fade-in max-h-60 overflow-auto">
          {options.map((opt, i) => {
            const isSelected = opt.value === value;
            const isHi = i === highlight && !opt.disabled;
            return (
              <button
                key={opt.value}
                type="button"
                disabled={opt.disabled}
                aria-disabled={opt.disabled}
                onMouseEnter={() => { if (!opt.disabled) setHighlight(i); }}
                onClick={() => choose(opt.value)}
                className={`w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-xs text-left transition-colors ${
                  opt.disabled
                    ? 'text-text-muted opacity-40 cursor-not-allowed'
                    : isHi ? 'bg-blue-500/15 text-text-primary' : 'text-text-secondary'
                } ${isSelected && !opt.disabled ? 'font-semibold text-text-primary' : ''}`}
              >
                <span className="truncate">{opt.label}</span>
                {opt.badge && (
                  <span className="shrink-0 px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider border border-current">
                    {opt.badge}
                  </span>
                )}
                {isSelected && !opt.disabled && <Check className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
