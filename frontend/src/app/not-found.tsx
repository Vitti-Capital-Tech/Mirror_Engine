'use client';
import React from 'react';
import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center select-none">
      <h2 className="text-5xl font-extrabold bg-gradient-to-r from-blue-400 to-purple-400 bg-clip-text text-transparent mb-3">
        404
      </h2>
      <p className="text-text-secondary mb-6 text-sm">
        The requested trading terminal screen was not found.
      </p>
      <Link 
        href="/" 
        className="px-4 py-2 bg-blue-600 hover:bg-blue-500 active:bg-blue-700 text-white text-xs font-semibold rounded-lg transition-all duration-200"
      >
        Return to Dashboard
      </Link>
    </div>
  );
}
