import { useState, useEffect } from "react";
import { useApi } from "../hooks/useApi";
import { useWebSocket } from "../hooks/useWebSocket";
import StatCard from "../components/StatCard";
import EquityChart from "../components/EquityChart";
import TradeRow from "../components/TradeRow";
import PnlBadge from "../components/PnlBadge";
import type { TradingSession, Trade, Position, EquitySnapshot } from "../types";

export default function DashboardPage() {
  const { data: session, refetch: refetchSession } = useApi<TradingSession>("/api/sessions/today");
  const { data: trades, refetch: refetchTrades } = useApi<Trade[]>("/api/trades/today");
  const { data: positions, refetch: refetchPositions } = useApi<Position[]>("/api/positions");
  const { data: equityCurve, refetch: refetchEquity } =
    useApi<EquitySnapshot[]>("/api/performance/equity-curve?days=1");

  const { on } = useWebSocket();

  useEffect(() => {
    const unsub = [
      on("trade_entry", () => { refetchTrades(); refetchPositions(); }),
      on("trade_exit",  () => { refetchTrades(); refetchPositions(); refetchSession(); }),
      on("equity_update", () => refetchEquity()),
      on("session_start", () => refetchSession()),
      on("daily_halt", () => refetchSession()),
    ];
    return () => unsub.forEach((fn) => fn());
  }, [on]);

  const dayPnl = session?.realizedPnl ?? 0;
  const accuracy = session?.accuracyPct?.toFixed(1) ?? "—";
  const unrealizedTotal = positions?.reduce((s, p) => s + (p.unrealizedPnl ?? 0), 0) ?? 0;
  const totalPnl = dayPnl + unrealizedTotal;

  return (
    <div className="space-y-6">
      {/* Header row */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Today's Session</h1>
        <div className="flex items-center gap-3">
          {session?.halted && (
            <span className="badge-red text-sm px-3 py-1">⛔ HALTED — {session.haltReason}</span>
          )}
          <span className={`badge-${session ? "green" : "gray"} text-sm px-3 py-1`}>
            {session ? (session.tradingMode === "paper" ? "📄 Paper" : "💰 Live") : "No session"}
          </span>
        </div>
      </div>

      {/* Key stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Day P&L (realized)"
          value={`${dayPnl >= 0 ? "+" : ""}$${dayPnl.toFixed(2)}`}
          color={dayPnl > 0 ? "green" : dayPnl < 0 ? "red" : "default"}
          sub={`Unrealized: ${unrealizedTotal >= 0 ? "+" : ""}$${unrealizedTotal.toFixed(2)}`}
        />
        <StatCard
          label="Trades"
          value={session?.totalTrades ?? 0}
          sub={`W: ${session?.winningTrades ?? 0}  L: ${session?.losingTrades ?? 0}`}
        />
        <StatCard
          label="Accuracy"
          value={accuracy === "—" ? "—" : `${accuracy}%`}
          color={parseFloat(accuracy) >= 60 ? "green" : parseFloat(accuracy) >= 40 ? "yellow" : "red"}
          sub="target ≥ 68%"
        />
        <StatCard
          label="Account Equity"
          value={session ? `$${(session.endingEquity ?? session.startingEquity).toLocaleString()}` : "—"}
          sub={session ? `Started: $${session.startingEquity.toLocaleString()}` : undefined}
        />
      </div>

      {/* Equity chart */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-300 mb-4">Intraday Equity</h2>
        <EquityChart data={equityCurve ?? []} />
      </div>

      {/* Open positions */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-300 mb-4">
          Open Positions
          <span className="ml-2 badge-yellow">{positions?.length ?? 0}</span>
        </h2>
        {!positions?.length ? (
          <p className="text-gray-600 text-sm">No open positions</p>
        ) : (
          <div className="space-y-3">
            {positions.map((p) => (
              <div key={p.id} className="flex items-center justify-between bg-gray-800 rounded-lg px-4 py-3">
                <div>
                  <span className="font-mono font-bold text-white text-lg">{p.symbol}</span>
                  <span className="ml-3 text-gray-500 text-sm">{p.qty} shares</span>
                  {p.setup && <span className="ml-2 badge-gray">{p.setup.replace(/_/g, " ")}</span>}
                </div>
                <div className="text-right">
                  <div className="font-mono text-sm text-gray-400">
                    Entry <span className="text-white">${p.entryPrice.toFixed(2)}</span>
                    {p.currentPrice && (
                      <> → <span className="text-white">${p.currentPrice.toFixed(2)}</span></>
                    )}
                  </div>
                  <div className="text-sm">
                    <span className="text-gray-500 text-xs">Stop </span>
                    <span className="font-mono text-red-400">${p.stopPrice.toFixed(2)}</span>
                    <span className="mx-2 text-gray-600">|</span>
                    <span className="text-gray-500 text-xs">Target </span>
                    <span className="font-mono text-green-400">${p.targetPrice.toFixed(2)}</span>
                  </div>
                  {p.unrealizedPnl != null && <PnlBadge value={p.unrealizedPnl} />}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Today's trades */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-300 mb-4">Today's Trades</h2>
        {!trades?.length ? (
          <p className="text-gray-600 text-sm">No trades today</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 text-xs uppercase tracking-wider border-b border-gray-800">
                  <th className="py-2 px-3">Symbol</th>
                  <th className="py-2 px-3">Setup</th>
                  <th className="py-2 px-3">Qty</th>
                  <th className="py-2 px-3">Entry</th>
                  <th className="py-2 px-3">Exit</th>
                  <th className="py-2 px-3">P&L</th>
                  <th className="py-2 px-3">Hold</th>
                  <th className="py-2 px-3">Status</th>
                  <th className="py-2 px-3">Time</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => <TradeRow key={t.id} trade={t} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
