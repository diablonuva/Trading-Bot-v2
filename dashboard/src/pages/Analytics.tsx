import { useApi } from "../hooks/useApi";
import StatCard from "../components/StatCard";
import {
  BarChart, Bar, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, CartesianGrid,
} from "recharts";
import { format } from "date-fns";
import type { PerformanceSummary, TradingSession, EquitySnapshot } from "../types";

export default function AnalyticsPage() {
  const { data: summary } = useApi<PerformanceSummary>("/api/performance/summary");
  const { data: daily } = useApi<TradingSession[]>("/api/performance/daily?days=30");
  const { data: equity } = useApi<EquitySnapshot[]>("/api/performance/equity-curve?days=30");

  const dailyChart = daily?.map((s) => ({
    date: format(new Date(s.date), "MM/dd"),
    pnl: s.realizedPnl,
    trades: s.totalTrades,
  })) ?? [];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold">Analytics</h1>

      {/* All-time stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Trades"   value={summary?.totalTrades ?? "—"} />
        <StatCard label="Accuracy"
          value={summary ? `${summary.accuracyPct.toFixed(1)}%` : "—"}
          color={summary && summary.accuracyPct >= 60 ? "green" : "yellow"}
          sub="target ≥ 68%" />
        <StatCard label="Total P&L"
          value={summary ? `$${summary.totalPnl.toFixed(2)}` : "—"}
          color={summary && summary.totalPnl > 0 ? "green" : "red"} />
        <StatCard label="Profit Factor"
          value={summary ? summary.profitFactor.toFixed(2) : "—"}
          color={summary && summary.profitFactor >= 1.5 ? "green" : "yellow"}
          sub="gross wins / gross losses" />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Avg Winner"
          value={summary ? `+$${summary.avgWinner.toFixed(2)}` : "—"}
          color="green" />
        <StatCard label="Avg Loser"
          value={summary ? `-$${Math.abs(summary.avgLoser).toFixed(2)}` : "—"}
          color="red" />
        <StatCard label="Win Trades"   value={summary?.winningTrades ?? "—"} color="green" />
        <StatCard label="Loss Trades"  value={summary?.losingTrades ?? "—"}  color="red" />
      </div>

      {/* Daily P&L bar chart */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-300 mb-4">Daily P&L — Last 30 Days</h2>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={dailyChart} margin={{ top: 5, right: 10, bottom: 0, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="date" tick={{ fill: "#6b7280", fontSize: 10 }} />
            <YAxis tick={{ fill: "#6b7280", fontSize: 11 }}
              tickFormatter={(v) => `$${v}`} />
            <Tooltip
              contentStyle={{ background: "#111827", border: "1px solid #374151", borderRadius: 8 }}
              formatter={(v: number) => [`$${v.toFixed(2)}`, "P&L"]}
            />
            <ReferenceLine y={0} stroke="#374151" />
            <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
              {dailyChart.map((d, i) => (
                <Cell key={i} fill={d.pnl >= 0 ? "#22c55e" : "#ef4444"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
