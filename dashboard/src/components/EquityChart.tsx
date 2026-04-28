import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { format } from "date-fns";
import type { EquitySnapshot } from "../types";

interface Props {
  data: EquitySnapshot[];
}

export default function EquityChart({ data }: Props) {
  if (!data.length) return (
    <div className="h-48 flex items-center justify-center text-gray-600 text-sm">
      No equity data yet
    </div>
  );

  const chartData = data.map((s) => ({
    time: format(new Date(s.timestamp), "HH:mm"),
    equity: s.equity,
    pnl: s.dayPnl ?? 0,
  }));

  const minE = Math.min(...chartData.map((d) => d.equity));
  const maxE = Math.max(...chartData.map((d) => d.equity));
  const domain = [minE * 0.999, maxE * 1.001];

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 10 }}>
        <defs>
          <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="#22c55e" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0}   />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
        <XAxis dataKey="time" tick={{ fill: "#6b7280", fontSize: 11 }} />
        <YAxis domain={domain} tick={{ fill: "#6b7280", fontSize: 11 }}
          tickFormatter={(v) => `$${v.toLocaleString()}`} width={80} />
        <Tooltip
          contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
          labelStyle={{ color: "#9ca3af" }}
          formatter={(v: number) => [`$${v.toLocaleString()}`, "Equity"]}
        />
        <Area type="monotone" dataKey="equity" stroke="#22c55e" strokeWidth={2}
          fill="url(#equityGrad)" dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
