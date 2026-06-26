'use client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { Toaster } from 'react-hot-toast';

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5000,
        retry: 2,
        refetchOnWindowFocus: false,
      }
    }
  }));
  return (
    <QueryClientProvider client={queryClient}>
      {children}
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 4000,
          style: {
            background: '#1a1d24',
            color: '#e5e7eb',
            border: '1px solid #2a2e37',
            fontSize: '13px',
            fontWeight: 500,
          },
          success: { iconTheme: { primary: '#34d399', secondary: '#1a1d24' } },
          error: { iconTheme: { primary: '#f87171', secondary: '#1a1d24' } },
        }}
      />
    </QueryClientProvider>
  );
}
