'use client';
import React from 'react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
} from 'recharts';

export function SlippageChart({ trades = [] }: { trades?: any[] }) {
  // Format data for Recharts: average slippage (%) per trade in chronological order
  const data = [...trades]
    .filter((t) => t.copies && t.copies.length > 0)
    .map((t) => {
      const copies = t.copies || [];
      const validSlippages = copies
        .filter((c: any) => c.status === 'filled' && c.slippage_pct !== null)
        .map((c: any) => Number(c.slippage_pct) * 100); // convert to %

      const avgSlippage =
        validSlippages.length > 0
          ? validSlippages.reduce((sum, val) => sum + val, 0) / validSlippages.length
          : 0;

      const dateObj = new Date(t.created_at);
      const label = dateObj.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      });

      return {
        time: label,
        slippage: Number(avgSlippage.toFixed(4)),
        symbol: t.symbol,
      };
    })
    .reverse(); // Oldest to newest

  const CustomTooltip = ({ active, payload }: any) => {
    if (active && payload && payload.length) {
      const dataPoint = payload[0].payload;
      return (
        <div className="bg-bg-secondary border border-bg-border p-3 rounded-lg text-xs shadow-xl select-none">
          <p className="text-text-muted mb-1 font-semibold">{dataPoint.time}</p>
          <p className="text-text-primary mb-0.5">Asset: <span className="font-bold">{dataPoint.symbol}</span></p>
          <p className="text-blue-400 font-bold">
            Slippage: {dataPoint.slippage.toFixed(4)}%
          </p>
        </div>
      );
    }
    return null;
  };

  return (
    <div className="bg-bg-panel border border-bg-border rounded-xl p-6 h-[320px] flex flex-col justify-between">
      <div className="flex items-center justify-between mb-4 select-none">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">Average Copy Slippage</h3>
          <p className="text-xs text-text-muted mt-0.5">Real-time slippage vs. 0.03% limit</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 bg-blue-500/20 border border-blue-500 rounded" />
          <span className="text-[10px] text-text-secondary font-bold uppercase tracking-wider">Avg Slippage</span>
        </div>
      </div>

      <div className="flex-1 w-full text-xs">
        {data.length === 0 ? (
          <div className="w-full h-full flex items-center justify-center text-text-muted select-none">
            No trade executions logged today.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="colorSlippage" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--bg-border)" vertical={false} />
              <XAxis 
                dataKey="time" 
                stroke="#475569" 
                tickLine={false} 
                axisLine={false} 
                dy={10}
              />
              <YAxis 
                stroke="#475569" 
                tickLine={false} 
                axisLine={false} 
                domain={[0, (dataMax: number) => Math.max(0.04, dataMax * 1.2)]}
                tickFormatter={(value) => `${value.toFixed(2)}%`}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ stroke: 'var(--bg-border)' }} />
              <ReferenceLine 
                y={0.03} 
                stroke="#ef4444" 
                strokeDasharray="4 4" 
                label={{ 
                  value: '0.03% Limit', 
                  fill: '#ef4444', 
                  position: 'top',
                  fontSize: 10,
                  fontWeight: 'bold'
                }} 
              />
              <Area
                type="monotone"
                dataKey="slippage"
                stroke="#3b82f6"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#colorSlippage)"
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
