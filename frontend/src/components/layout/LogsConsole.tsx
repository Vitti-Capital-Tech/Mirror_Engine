'use client';
import React, { useEffect, useState, useRef } from 'react';
import { useSocket } from '@/hooks/useSocket';
import { Terminal, ChevronUp, ChevronDown, Trash2 } from 'lucide-react';

interface LogItem {
  id: string;
  time: string;
  level: 'info' | 'warning' | 'error';
  message: string;
}

export function LogsConsole() {
  const { isConnected, latestTrade, latestPosition, latestAlert } = useSocket();
  const [isMaximized, setIsMaximized] = useState(false);
  const [logs, setLogs] = useState<LogItem[]>([
    {
      id: 'init-1',
      time: new Date().toLocaleTimeString(),
      level: 'info',
      message: 'Mirror Engine UI console initialized.',
    },
  ]);
  const consoleBottomRef = useRef<HTMLDivElement>(null);
  // Track which account+symbol are already flagged out-of-sync so we log each
  // mismatch only once (until it recovers) instead of every sync cycle.
  const outOfSyncRef = useRef<Set<string>>(new Set());

  const addLog = (message: string, level: 'info' | 'warning' | 'error' = 'info') => {
    const newLog: LogItem = {
      id: Math.random().toString(),
      time: new Date().toLocaleTimeString(),
      level,
      message,
    };
    setLogs((prev) => [newLog, ...prev].slice(0, 100)); // Keep last 100 logs
  };

  // Listen to connection
  useEffect(() => {
    addLog(
      isConnected ? 'WebSocket server connected successfully.' : 'WebSocket disconnected. Retrying connection...',
      isConnected ? 'info' : 'warning'
    );
  }, [isConnected]);

  // Listen to trade fills
  useEffect(() => {
    if (latestTrade) {
      const copiesCount = latestTrade.copies?.length || 0;
      addLog(
        `Master trade filled: ${latestTrade.side.toUpperCase()} ${latestTrade.symbol} - Scale size: ${latestTrade.quantity} - Copying to ${copiesCount} followers`,
        'info'
      );
    }
  }, [latestTrade]);

  // Listen to position updates — surface each mismatch ONCE (log on the
  // transition into out-of-sync, and again only after it has recovered).
  useEffect(() => {
    if (!latestPosition) return;
    const status = latestPosition.sync_status?.toLowerCase();
    const key = `${latestPosition.account_id}:${latestPosition.symbol}`;
    const seen = outOfSyncRef.current;
    if (status === 'out_of_sync' || status === 'desynced') {
      if (!seen.has(key)) {
        seen.add(key);
        addLog(
          `Position OUT OF SYNC: ${latestPosition.account_name} on ${latestPosition.symbol} (size=${latestPosition.quantity})`,
          'warning'
        );
      }
    } else {
      seen.delete(key); // recovered — allow a future mismatch to log again
    }
  }, [latestPosition]);

  // Listen to alerts
  useEffect(() => {
    if (latestAlert) {
      const level = latestAlert.level === 'critical' ? 'error' : latestAlert.level === 'warning' ? 'warning' : 'info';
      addLog(`SYSTEM ALERT: ${latestAlert.message}`, level);
    }
  }, [latestAlert]);

  // Scroll to bottom on maximization/new log
  useEffect(() => {
    if (isMaximized && consoleBottomRef.current) {
      consoleBottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [isMaximized, logs]);

  const clearLogs = () => {
    setLogs([
      {
        id: 'clear-1',
        time: new Date().toLocaleTimeString(),
        level: 'info',
        message: 'Console cleared.',
      },
    ]);
  };

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'error':
        return 'text-red-400 font-bold';
      case 'warning':
        return 'text-amber-400 font-semibold';
      default:
        return 'text-blue-400';
    }
  };

  const latestLog = logs[0] || null;

  return (
    <div
      className={`border-t border-bg-border bg-bg-panel transition-all duration-300 w-full select-none select-text ${
        isMaximized ? 'h-56' : 'h-10'
      } flex flex-col`}
    >
      {/* Ticker / Top bar header */}
      <div className="h-10 px-6 bg-bg-secondary border-b border-bg-border flex items-center justify-between select-none">
        <div
          className="flex items-center gap-2 cursor-pointer flex-1 min-w-0"
          onClick={() => setIsMaximized(!isMaximized)}
        >
          <Terminal className="w-4 h-4 text-text-muted shrink-0" />
          <span className="text-[10px] font-bold text-text-muted tracking-wider uppercase shrink-0">Live Logger:</span>
          {latestLog && (
            <div className="flex items-center gap-2 text-xs font-mono truncate pl-1">
              <span className="text-text-muted text-[10px] shrink-0">[{latestLog.time}]</span>
              <span className={`${getLevelColor(latestLog.level)} text-[10px] uppercase shrink-0`}>
                {latestLog.level}
              </span>
              <span className="text-text-primary truncate">{latestLog.message}</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          {isMaximized && (
            <button
              onClick={clearLogs}
              className="p-1 text-text-muted hover:text-white rounded transition-colors"
              title="Clear logs"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={() => setIsMaximized(!isMaximized)}
            className="p-1 text-text-secondary hover:text-text-primary rounded transition-colors"
            title={isMaximized ? 'Minimize' : 'Maximize'}
          >
            {isMaximized ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Extended logs area */}
      {isMaximized && (
        <div className="flex-1 overflow-auto p-4 bg-bg-panel font-mono text-[11px] space-y-1">
          {logs
            .slice()
            .reverse()
            .map((log) => (
              <div key={log.id} className="flex gap-2 leading-relaxed hover:bg-bg-secondary/40 px-1 rounded transition-colors">
                <span className="text-text-muted select-none">[{log.time}]</span>
                <span className={`${getLevelColor(log.level)} uppercase select-none w-14 inline-block`}>
                  {log.level}
                </span>
                <span className="text-text-secondary flex-1">{log.message}</span>
              </div>
            ))}
          <div ref={consoleBottomRef} />
        </div>
      )}
    </div>
  );
}
