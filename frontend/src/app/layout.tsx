import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import './globals.css';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { LogsConsole } from '@/components/layout/LogsConsole';
import Providers from './providers';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'Mirror Engine | Delta Exchange',
  description: 'Institutional copy trading platform for Delta Exchange India',
  icons: {
    icon: '/logo.jpg',
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang='en' className="dark">
      <head>
        <script dangerouslySetInnerHTML={{ __html: `
          (function() {
            try {
              const theme = localStorage.getItem('theme') || 'dark';
              if (theme === 'dark') {
                document.documentElement.classList.add('dark');
                document.documentElement.classList.remove('light');
              } else {
                document.documentElement.classList.add('light');
                document.documentElement.classList.remove('dark');
              }
            } catch (e) {}
          })();
        `}} />
      </head>
      <body className={`${inter.className} overflow-hidden`}>
        <Providers>
          <div className="flex h-screen w-screen overflow-hidden text-text-primary">
            <Sidebar />
            <div className="flex-1 flex flex-col overflow-hidden min-w-0">
              <TopBar />
              <main className="flex-1 overflow-auto p-6 bg-bg-primary">
                {children}
              </main>
              <LogsConsole />
            </div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
