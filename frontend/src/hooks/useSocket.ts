'use client';
import { useEffect, useState } from 'react';
import { getSocket } from '@/lib/socket';

export interface TradeEvent {
  id: string;
  master_trade_id: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  trade_type: string;
  status: string;
  created_at: string;
  copies: any[];
}

export interface PositionEvent {
  id?: string;
  account_id: string;
  account_name: string;
  symbol: string;
  side: string;
  quantity: number;
  entry_price: number;
  current_price: number;
  unrealized_pnl: number;
  sync_status: string;
  last_synced_at?: string;
}

export interface AlertEvent {
  id: string;
  level: string;
  type: string;
  account_id?: string;
  account_name?: string;
  message: string;
  metadata?: any;
  is_resolved: boolean;
  created_at: string;
}

export function useSocket() {
  const [isConnected, setIsConnected] = useState(false);
  const [latestTrade, setLatestTrade] = useState<TradeEvent | null>(null);
  const [latestPosition, setLatestPosition] = useState<PositionEvent | null>(null);
  const [latestAlert, setLatestAlert] = useState<AlertEvent | null>(null);
  const [systemStatus, setSystemStatus] = useState<any>(null);

  useEffect(() => {
    const socket = getSocket();
    
    setIsConnected(socket.connected);

    function onConnect() {
      setIsConnected(true);
    }

    function onDisconnect() {
      setIsConnected(false);
    }

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('trade_copy', (data: TradeEvent) => {
      setLatestTrade(data);
    });
    socket.on('position_update', (data: PositionEvent) => {
      setLatestPosition(data);
    });
    socket.on('alert', (data: AlertEvent) => {
      setLatestAlert(data);
    });
    socket.on('system_status', (data: any) => {
      setSystemStatus(data);
    });
    
    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('trade_copy');
      socket.off('position_update');
      socket.off('alert');
      socket.off('system_status');
    };
  }, []);

  return { isConnected, latestTrade, latestPosition, latestAlert, systemStatus };
}
