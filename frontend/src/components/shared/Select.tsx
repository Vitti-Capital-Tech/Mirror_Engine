'use client';
import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, Check } from 'lucide-react';

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  className?: string;
  placeholder?: string;
  size?: 'sm' | 'md';
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
      const idx = options.findIndex((o) => o.value === value);
      setHighlight(idx >= 0 ? idx : 0);
    }
  }, [open, value, options]);

  const pad = size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3 py-2 text-xs';

  const choose = (v: string) => {
    onChange(v);
    setOpen(false);
  };

  return (
    <div className={`relative ${className}`} ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setHighlight((h) => Math.min(h + 1, options.length - 1)); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setHighlight((h) => Math.max(h - 1, 0)); }
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
            const isHi = i === highlight;
            return (
              <button
                key={opt.value}
                type="button"
                onMouseEnter={() => setHighlight(i)}
                onClick={() => choose(opt.value)}
                className={`w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-xs text-left transition-colors ${
                  isHi ? 'bg-blue-500/15 text-text-primary' : 'text-text-secondary'
                } ${isSelected ? 'font-semibold text-text-primary' : ''}`}
              >
                <span className="truncate">{opt.label}</span>
                {isSelected && <Check className="w-3.5 h-3.5 text-blue-400 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
