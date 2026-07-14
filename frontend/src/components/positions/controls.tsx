'use client';
import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, ChevronLeft, ChevronRight, Calendar as CalendarIcon, X } from 'lucide-react';

// ── Portal popover anchored to a trigger element ──────────────────────────
// Rendered into <body> so the card's overflow-hidden never clips it. Follows
// the trigger on scroll/resize and closes on outside click or Escape.
function Popover({ anchorRef, open, onClose, direction = 'down', align = 'left', width, children }: {
  anchorRef: React.RefObject<HTMLElement>;
  open: boolean;
  onClose: () => void;
  direction?: 'up' | 'down';
  align?: 'left' | 'right';
  width?: number;
  children: React.ReactNode;
}) {
  const popRef = useRef<HTMLDivElement>(null);
  const [style, setStyle] = useState<React.CSSProperties | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const el = anchorRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const w = width ?? r.width;
      const left = Math.max(8, Math.min(
        align === 'right' ? r.right - w : r.left,
        window.innerWidth - w - 8,
      ));
      const base: React.CSSProperties = { position: 'fixed', left, zIndex: 70, minWidth: w };
      if (direction === 'up') base.bottom = window.innerHeight - r.top + 6;
      else base.top = r.bottom + 6;
      setStyle(base);
    };
    place();
    window.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
    };
  }, [open, direction, align, width, anchorRef]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (anchorRef.current?.contains(t) || popRef.current?.contains(t)) return;
      onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose, anchorRef]);

  if (!open || !style || typeof document === 'undefined') return null;
  return createPortal(
    <div ref={popRef} style={style}
      className="rounded-lg border border-bg-border bg-bg-panel shadow-2xl shadow-black/50 backdrop-blur-sm overflow-hidden">
      {children}
    </div>,
    document.body,
  );
}

// ── Rows-per-page selector (custom dropdown) ──────────────────────────────
export function PageSizeSelect({ value, onChange, options }: {
  value: number; onChange: (n: number) => void; options: number[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button
        ref={ref}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 bg-bg-panel border border-bg-border text-text-secondary rounded-md pl-2.5 pr-1.5 py-1 text-[11px] font-semibold hover:border-blue-400/40 hover:text-text-primary transition-colors"
      >
        {value} / Page
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      <Popover anchorRef={ref} open={open} onClose={() => setOpen(false)} direction="up" width={96}>
        <div className="py-1">
          {options.map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => { onChange(n); setOpen(false); }}
              className={`block w-full text-left px-3 py-1.5 text-[11px] font-semibold transition-colors ${
                n === value ? 'text-blue-400 bg-blue-500/10' : 'text-text-secondary hover:bg-bg-secondary/40 hover:text-text-primary'
              }`}
            >
              {n} / Page
            </button>
          ))}
        </div>
      </Popover>
    </>
  );
}

// ── Date filter with a custom calendar popover ────────────────────────────
const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
const ymd = (y: number, m: number, d: number) =>
  `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;

export function DateFilter({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  const today = new Date();
  const selected = value ? new Date(value + 'T00:00:00') : null;
  const [view, setView] = useState(() => {
    const b = selected ?? today;
    return { y: b.getFullYear(), m: b.getMonth() };
  });

  const first = new Date(view.y, view.m, 1);
  const startWeekday = first.getDay();
  const daysInMonth = new Date(view.y, view.m + 1, 0).getDate();
  const cells: (number | null)[] = [];
  for (let i = 0; i < startWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  const monthLabel = first.toLocaleString('en-US', { month: 'long', year: 'numeric' });
  const isSel = (d: number) => !!value && value === ymd(view.y, view.m, d);
  const isToday = (d: number) =>
    today.getFullYear() === view.y && today.getMonth() === view.m && today.getDate() === d;
  const shiftMonth = (delta: number) =>
    setView((v) => {
      const nm = v.m + delta;
      return { y: v.y + Math.floor(nm / 12), m: ((nm % 12) + 12) % 12 };
    });

  const label = selected
    ? selected.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
    : 'All Dates';

  return (
    <>
      <div className="inline-flex items-center gap-1">
        <button
          ref={ref}
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 bg-bg-panel border border-bg-border text-text-secondary rounded-md px-2.5 py-1 text-[11px] font-semibold hover:border-blue-400/40 hover:text-text-primary transition-colors"
        >
          <CalendarIcon className="w-3.5 h-3.5" />
          {label}
        </button>
        {value && (
          <button
            type="button"
            onClick={() => onChange('')}
            title="Clear date filter"
            className="flex items-center justify-center w-6 h-6 rounded-md border border-bg-border text-text-muted hover:text-text-secondary hover:bg-bg-panel transition-colors"
          >
            <X className="w-3 h-3" />
          </button>
        )}
      </div>

      <Popover anchorRef={ref} open={open} onClose={() => setOpen(false)} direction="down" align="right" width={236}>
        <div className="p-3 w-[236px] select-none">
          {/* Month nav */}
          <div className="flex items-center justify-between mb-2">
            <button type="button" onClick={() => shiftMonth(-1)}
              className="flex items-center justify-center w-6 h-6 rounded-md border border-bg-border text-text-secondary hover:bg-bg-secondary/40 transition-colors">
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <span className="text-xs font-bold text-text-primary">{monthLabel}</span>
            <button type="button" onClick={() => shiftMonth(1)}
              className="flex items-center justify-center w-6 h-6 rounded-md border border-bg-border text-text-secondary hover:bg-bg-secondary/40 transition-colors">
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Weekday header */}
          <div className="grid grid-cols-7 gap-0.5 mb-1">
            {WEEKDAYS.map((w, i) => (
              <div key={i} className="text-center text-[9px] font-bold text-text-muted uppercase py-0.5">{w}</div>
            ))}
          </div>

          {/* Day grid */}
          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((d, i) => d == null ? <div key={i} /> : (
              <button
                key={i}
                type="button"
                onClick={() => { onChange(ymd(view.y, view.m, d)); setOpen(false); }}
                className={`h-7 rounded-md text-[11px] font-semibold transition-colors ${
                  isSel(d)
                    ? 'bg-blue-500 text-white'
                    : isToday(d)
                      ? 'text-blue-400 ring-1 ring-inset ring-blue-400/50 hover:bg-blue-500/10'
                      : 'text-text-secondary hover:bg-bg-secondary/40 hover:text-text-primary'
                }`}
              >
                {d}
              </button>
            ))}
          </div>

          {/* Footer actions */}
          <div className="flex items-center justify-between mt-3 pt-2 border-t border-bg-border">
            <button type="button"
              onClick={() => { const t = new Date(); setView({ y: t.getFullYear(), m: t.getMonth() }); onChange(ymd(t.getFullYear(), t.getMonth(), t.getDate())); setOpen(false); }}
              className="px-2 py-1 text-[10px] font-bold text-blue-400 hover:text-blue-300 transition-colors">
              Today
            </button>
            <button type="button"
              onClick={() => { onChange(''); setOpen(false); }}
              className="px-2 py-1 text-[10px] font-bold text-text-muted hover:text-text-secondary transition-colors">
              All Dates
            </button>
          </div>
        </div>
      </Popover>
    </>
  );
}
