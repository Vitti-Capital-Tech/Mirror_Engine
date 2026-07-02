'use client';
import React, { useState, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useTrades } from '@/hooks/useTrades';
import { useSocket } from '@/hooks/useSocket';
import { TradeLogTable } from '@/components/trades/TradeLogTable';
import { Loader } from '@/components/shared/Loader';

export default function TradesPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const limit = 20;

  const { data: trades = [], isLoading } = useTrades({ page, limit });
  const { latestTrade } = useSocket();

  // WS trade copy trigger refresh
  useEffect(() => {
    if (latestTrade) {
      queryClient.invalidateQueries({ queryKey: ['trades'] });
      queryClient.invalidateQueries({ queryKey: ['trade-stats'] });
    }
  }, [latestTrade, queryClient]);

  return (
    <div className="space-y-6">
      {/* Trade log table */}
      {isLoading ? (
        <Loader label="Loading trades…" />
      ) : (
        <TradeLogTable
          trades={trades}
          isLoading={false}
          page={page}
          setPage={setPage}
          hasMore={trades.length === limit}
        />
      )}
    </div>
  );
}
