import { useState, useEffect, useMemo } from "react";
import { useApi } from "../hooks/useApi";
import { useWebSocket } from "../hooks/useWebSocket";
import StatCard from "../components/StatCard";
import EquityChart from "../components/EquityChart";
import MarketChart from "../components/MarketChart";
import TradeRow from "../components/TradeRow";
import PnlBadge from "../components/PnlBadge";
import TradeEntryGates from "../components/TradeEntryGates";
import type { TradingSession, Trade, Position, EquitySnapshot } from "../types";

const PORTFOLIO_RANGES = [
  { label: "1D",  period: "1D",  timeframe: "5Min" },
  { label: "1W",  period: "1W",  timeframe: "15Min" },
  { label: "1M",  period: "1M",  timeframe: "1H" },
  { label: "3M",  period: "3M",  timeframe: "1D" },
  { label: "1Y",  period: "1A",  timeframe: "1D" },
  { label: "ALL", period: "all", timeframe: "1D" },
] as const;

interface AlpacaAccount {
  mode: string;
  equity: number;
  lastEquity: number;
  cash: number;
  buyingPower: number;
  portfolioValue: number;
  daytradeCount: number;
  dayPnl: number;
  dayPnlPct: number;
  status: string;
  currency: string;
}

interface PortfolioHistory {
  period: string;
  timeframe: string;
  baseValue: number;
  points: { timestamp: string; equity: number; dayPnl: number; pnlPct: number }[];
}

interface AlpacaPosition {
  symbol: string;
  qty: number;
  side: string;
  avgEntryPrice: number;
  currentPrice: number;
  marketValue: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  changeToday: number;
}

type ScanCandidate = {
  symbol: string;
  pctChange: number;
  relativeVolume: number;
  hasNews: boolean;
  score: number;
};

type ScanResult = {
  candidates: ScanCandidate[];
};

function getBotStatus(session: TradingSession | null | undefined): { label: string; color: string } {
  if (!session) return { label: "INACTIVE", color: "gray" };
  if (session.halted) return { label: "HALTED", color: "red" };
  if (!session.endedAt) return { label: "RUNNING", color: "green" };
  return { label: "INACTIVE", color: "gray" };
}

export default function DashboardPage() {
  const [rangeIdx, setRangeIdx] = useState<number>(0); // 0 = 1D
  const range = PORTFOLIO_RANGES[rangeIdx];

  const { data: session, refetch: refetchSession } = useApi<TradingSession>("/api/sessions/today");
  const { data: trades, refetch: refetchTrades } = useApi<Trade[]>("/api/trades/today");
  const { data: botPositions, refetch: refetchBotPositions } = useApi<Position[]>("/api/positions");
  const { data: scanResult, refetch: refetchScan } = useApi<ScanResult>("/api/scanner/latest");

  // Live Alpaca state
  const { data: account, refetch: refetchAccount } =
    useApi<AlpacaAccount>("/api/portfolio/account");
  const { data: alpacaPositions, refetch: refetchAlpacaPositions } =
    useApi<AlpacaPosition[]>("/api/portfolio/positions");
  const { data: history, refetch: refetchHistory } =
    useApi<PortfolioHistory>(
      `/api/portfolio/history?period=${range.period}&timeframe=${range.timeframe}`,
      [rangeIdx],
    );

  const { on, connected } = useWebSocket();

  // Live polling: refresh Alpaca account + positions every 15s
  useEffect(() => {
    const id = setInterval(() => {
      refetchAccount();
      refetchAlpacaPositions();
    }, 15_000);
    return () => clearInterval(id);
  }, [refetchAccount, refetchAlpacaPositions]);

  // History refreshes once a minute
  useEffect(() => {
    const id = setInterval(() => refetchHistory(), 60_000);
    return () => clearInterval(id);
  }, [refetchHistory]);

  useEffect(() => {
    const unsub = [
      on("trade_entry", () => { refetchTrades(); refetchBotPositions(); refetchAlpacaPositions(); refetchAccount(); }),
      on("trade_exit",  () => { refetchTrades(); refetchBotPositions(); refetchAlpacaPositions(); refetchAccount(); refetchSession(); }),
      on("equity_update", () => refetchAccount()),
      on("session_start", () => refetchSession()),
      on("daily_halt", () => refetchSession()),
      on("scan_complete", () => refetchScan()),
    ];
    return () => unsub.forEach((fn) => fn());
  }, [on]);

  const dayPnl = session?.realizedPnl ?? 0;
  const accuracy = session?.accuracyPct?.toFixed(1) ?? "—";
  const unrealizedTotal = alpacaPositions?.reduce((s, p) => s + p.unrealizedPnl, 0) ?? 0;
  const botStatus = getBotStatus(session);
  const watchlist = scanResult?.candidates ?? [];

  // Live portfolio value comes straight from Alpaca
  const portfolioValue = account?.portfolioValue ?? 0;
  // Range change = first vs last point in the history series (Alpaca-supplied)
  const historyPoints = history?.points ?? [];
  const startValue = historyPoints[0]?.equity ?? account?.lastEquity ?? portfolioValue;
  const portfolioChange = portfolioValue - startValue;
  const portfolioChangePct = startValue > 0 ? (portfolioChange / startValue) * 100 : 0;

  // Adapter: Alpaca history points -> EquitySnapshot shape EquityChart expects
  const equityChartData: EquitySnapshot[] = historyPoints.map((p) => ({
    timestamp: p.timestamp,
    equity: p.equity,
    dayPnl: p.dayPnl,
  }));

  // Symbols available in the market chart dropdown
  const chartSymbols = useMemo(() => {
    const set = new Set<string>(["SPY", "QQQ", "IWM"]);
    alpacaPositions?.forEach((p) => set.add(p.symbol));
    botPositions?.forEach((p) => set.add(p.symbol));
    watchlist.forEach((c) => set.add(c.symbol));
    return Array.from(set);
  }, [alpacaPositions, botPositions, watchlist]);

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Live trade-entry gates — always at the top */}
      <TradeEntryGates
        session={session}
        account={account}
        watchlistCount={watchlist.length}
        wsConnected={connected}
      />

      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg sm:text-xl font-bold text-white">Today's Session</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`badge-${botStatus.color} text-xs sm:text-sm px-2 sm:px-3 py-1 font-mono font-bold`}>
            {botStatus.label}
          </span>
          {session?.halted && (
            <span className="badge-red text-xs sm:text-sm px-2 sm:px-3 py-1">HALTED — {session.haltReason}</span>
          )}
          <span className={`badge-${session ? "green" : "gray"} text-xs sm:text-sm px-2 sm:px-3 py-1`}>
            {session ? (session.tradingMode === "paper" ? "Paper" : "Live") : "No session"}
          </span>
        </div>
      </div>

      {/* Key stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
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
          value={account ? `$${account.equity.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : "—"}
          sub={account ? `Cash: $${account.cash.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : undefined}
        />
      </div>

      {/* Portfolio Value (live from Alpaca) */}
      <div className="card bg-gradient-to-br from-gray-900 to-gray-950 border-gray-800">
        <div className="flex items-end justify-between flex-wrap gap-4 mb-4">
          <div>
            <p className="stat-label flex items-center gap-2">
              Portfolio Value
              <span className="badge-gray text-[10px]">{account?.mode?.toUpperCase() ?? "PAPER"}</span>
              <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" title="Live from Alpaca" />
            </p>
            <p className="text-3xl sm:text-4xl font-bold font-mono text-white mt-1 break-all">
              ${portfolioValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </p>
            <p className={`text-sm font-mono mt-1 ${portfolioChange >= 0 ? "text-green-400" : "text-red-400"}`}>
              {portfolioChange >= 0 ? "+" : ""}${portfolioChange.toFixed(2)} ({portfolioChange >= 0 ? "+" : ""}{portfolioChangePct.toFixed(2)}%)
              <span className="text-gray-500 ml-2 text-xs">{range.label} change</span>
            </p>
            <p className="text-xs text-gray-500 mt-2">
              Cash <span className="text-gray-300 font-mono">${account?.cash.toLocaleString(undefined, { maximumFractionDigits: 2 }) ?? "—"}</span>
              <span className="mx-2">·</span>
              Buying Power <span className="text-gray-300 font-mono">${account?.buyingPower.toLocaleString(undefined, { maximumFractionDigits: 2 }) ?? "—"}</span>
              <span className="mx-2">·</span>
              Day Trades <span className="text-gray-300 font-mono">{account?.daytradeCount ?? 0}</span>
            </p>
          </div>
          <div className="flex gap-1 flex-wrap">
            {PORTFOLIO_RANGES.map((r, i) => (
              <button
                key={r.label}
                onClick={() => setRangeIdx(i)}
                className={`text-xs px-3 py-1 rounded font-mono ${
                  rangeIdx === i
                    ? "bg-green-700 text-white"
                    : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
        <EquityChart data={equityChartData} />
      </div>

      {/* Market chart (TradingView-style candlestick) */}
      <MarketChart defaultSymbol="SPY" symbols={chartSymbols} />


      {/* Open positions (live from Alpaca) */}
      <div className="card">
        <h2 className="text-sm font-semibold text-gray-300 mb-4 flex items-center gap-2">
          Open Positions
          <span className="badge-yellow">{alpacaPositions?.length ?? 0}</span>
          <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" title="Live from Alpaca" />
        </h2>
        {!alpacaPositions?.length ? (
          <p className="text-gray-600 text-sm">No open positions</p>
        ) : (
          <div className="space-y-3">
            {alpacaPositions.map((p) => {
              // Look up the bot-tracked stop/target if this position was opened by the bot
              const botRow = botPositions?.find((b) => b.symbol === p.symbol);
              return (
                <div key={p.symbol} className="flex flex-col sm:flex-row sm:items-center sm:justify-between bg-gray-800 rounded-lg px-3 sm:px-4 py-3 gap-2">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="font-mono font-bold text-white text-base sm:text-lg">{p.symbol}</span>
                    <span className="text-gray-500 text-sm">{p.qty} shares</span>
                    <span className="text-gray-600 text-xs">({p.side})</span>
                    {botRow?.setup && (
                      <span className="badge-gray">{botRow.setup.replace(/_/g, " ")}</span>
                    )}
                  </div>
                  <div className="sm:text-right">
                    <div className="font-mono text-sm text-gray-400">
                      Entry <span className="text-white">${p.avgEntryPrice.toFixed(2)}</span>
                      <> → <span className="text-white">${p.currentPrice.toFixed(2)}</span></>
                    </div>
                    {botRow && (
                      <div className="text-sm">
                        <span className="text-gray-500 text-xs">Stop </span>
                        <span className="font-mono text-red-400">${botRow.stopPrice.toFixed(2)}</span>
                        <span className="mx-2 text-gray-600">|</span>
                        <span className="text-gray-500 text-xs">Target </span>
                        <span className="font-mono text-green-400">${botRow.targetPrice.toFixed(2)}</span>
                      </div>
                    )}
                    <div className="flex items-center gap-2 sm:justify-end mt-1">
                      <PnlBadge value={p.unrealizedPnl} />
                      <span className={`text-xs font-mono ${p.unrealizedPnlPct >= 0 ? "text-green-400" : "text-red-400"}`}>
                        ({p.unrealizedPnlPct >= 0 ? "+" : ""}{p.unrealizedPnlPct.toFixed(2)}%)
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
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
