import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: 'var(--bg-primary)',
          secondary: 'var(--bg-secondary)',
          panel: 'var(--bg-panel)',
          border: 'var(--bg-border)',
          elevated: 'var(--bg-elevated)',
        },
        accent: {
          blue: '#3b82f6',
          'blue-glow': 'rgba(59,130,246,0.15)',
          green: '#10b981',
          'green-glow': 'rgba(16,185,129,0.15)',
          red: '#ef4444',
          yellow: '#f59e0b',
          purple: '#8b5cf6',
        },
        text: {
          primary: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          muted: 'var(--text-muted)',
        }
      },
      // THE DEFAULT BORDER COLOUR, or Tailwind's preflight uses its own
      // #e5e7eb — a near-white grey that is invisible on a light theme and
      // glaring on a dark one.
      //
      // That is not hypothetical. `divide-bg-border/70` generated NO rule at
      // all (the colours here are raw `var()` strings, which Tailwind v3
      // cannot apply an opacity modifier to), so `divide-y` set only the
      // border WIDTH and the colour fell through to #e5e7eb. The result was a
      // bright white line between two follower rows on an otherwise dark
      // page — visible in the UI on 2026-11-26, and present in six other
      // places written the same way.
      //
      // The broken classes are fixed at their call sites. This is the net
      // underneath them: any border whose colour is missing for any reason
      // now lands on the theme's own border instead of on white.
      borderColor: {
        DEFAULT: 'var(--bg-border)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'slide-in': 'slideIn 0.3s ease-out',
        'fade-in': 'fadeIn 0.2s ease-out',
        'blink': 'blink 1s ease-in-out infinite',
        'shimmer': 'shimmer 4.5s ease-in-out infinite',
      },
      keyframes: {
        slideIn: {
          '0%': { opacity: '0', transform: 'translateY(-10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        blink: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.3' },
        },
        shimmer: {
          '0%': { transform: 'translateX(-160%) skewX(-20deg)' },
          '55%, 100%': { transform: 'translateX(260%) skewX(-20deg)' },
        },
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;
