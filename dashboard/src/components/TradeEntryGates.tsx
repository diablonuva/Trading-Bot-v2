import { useEffect, useState } from "react";
import { useApi } from "../hooks/useApi";
import { useWebSocket } from "../hooks/useWebSocket";
import type { GateCheck } from "../types";

interface Props {
  session: { halted?: boolean; endedAt?: string; totalTrades?: number; tradingMode?: string } | null | undefined;
  account: { buyingPower?: number; daytradeCount?: number; mode?: string } | null | undefined;
  watchlistCount: number;
  wsConnected: boolean;
  maxTradesPerDay?: number;
}

interface Gate {
  name: string;
  ok: boolean;
}

interface GatesResponse {
  records: GateCheck[];
}

// Per-bar strategy gate names (from bot/src/strategy.py) → friendly labels
const PER_BAR_LABELS: { key: string; label: string }[] = [
  { key: "bars_ready",    label: "Bars ≥30" },
  { key: "macd_positive", label: "MACD > 0" },
  { key: "above_vwap",    label: "Above VWAP" },
  { key: "volume_surge",  label: "Vol Surge" },
  { key: "pattern_match", label: "Pattern" },
  { key: "a_quality",     label: "A Quality" },
];

function nyHourMinute(): { hour: number; minute: number; weekday: number } {
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  return { hour: ny.getHours(), minute: ny.getMinutes(), weekday: ny.getDay() };
}

function relativeAge(ts: string): string {
  const ms = Date.now() - new Date(ts).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

export default function TradeEntryGates({
  session,
  account,
  watchlistCount,
  wsConnected,
  maxTradesPerDay = 10,
}: Props) {
  // Re-render every 30s for the time-window gate flip
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  // Per-symbol gate state — initial fetch + WS push updates
  const { data: gatesData } = useApi<GatesResponse>("/api/gates/latest");
  const [perSymbol, setPerSymbol] = useState<GateCheck[]>([]);
  const { on } = useWebSocket();

  useEffect(() => {
    if (gatesData?.records) setPerSymbol(gatesData.records);
  }, [gatesData]);

  useEffect(() => {
    return on("gate_check", (data) => {
      const rec = data as GateCheck;
      setPerSymbol((prev) => {
        const filtered = prev.filter((r) => r.symbol !== rec.symbol);
        return [rec, ...filtered].slice(0, 10);
      });
    });
  }, [on]);

  // ---- Global gate computation ----
  const { hour, minute, weekday } = nyHourMinute();
  const isWeekday = weekday >= 1 && weekday <= 5;
  // Entry window: 07:00 ET (pre-market) through 15:00 ET (stop_entries).
  // Matches stop_entries_time in bot/config/settings.yaml.
  const inEntryWindow = isWeekday
    && (hour > 7 || (hour === 7 && minute >= 0))
    && hour < 15;
  const inMarketHours = isWeekday
    && (hour > 9 || (hour === 9 && minute >= 30))
    && hour < 16;

  const tradesUsed = session?.totalTrades ?? 0;
  const buyingPower = account?.buyingPower ?? 0;
  const dayTrades = account?.daytradeCount ?? 0;
  const sessionActive = !!session && !session.endedAt;

  const globalGates: Gate[] = [
    { name: "Entry Window",  ok: inEntryWindow },
    { name: "Market Open",   ok: inMarketHours },
    { name: "Bot Live",      ok: wsConnected },
    { name: "Session",       ok: sessionActive },
    { name: "Not Halted",    ok: !session?.halted },
    { name: "Watchlist",     ok: watchlistCount > 0 },
    { name: "Capital",       ok: buyingPower > 1000 },
    { name: `Slots ${tradesUsed}/${maxTradesPerDay}`, ok: tradesUsed < maxTradesPerDay },
    { name: `Day Trades ${dayTrades}/3`, ok: dayTrades < 3 },
  ];

  const blocked = globalGates.filter((g) => !g.ok).length;
  const total = globalGates.length;

  return (
    <div className="card bg-gray-900/80 border-gray-800">
      {/* Header */}
      <div className="flex items-center justify-between mb-2 sm:mb-3">
        <h2 className="text-[10px] sm:text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Trade Entry Gates
        </h2>
        <span className={`text-[10px] sm:text-xs font-mono font-bold ${
          blocked === 0 ? "text-green-400" : "text-yellow-400"
        }`}>
          {blocked === 0 ? `${total}/${total} PASS` : `${blocked}/${total} BLOCKED`}
        </span>
      </div>

      {/* Global gates */}
      <div className="grid grid-cols-3 gap-x-2 sm:gap-x-4 gap-y-1.5 sm:gap-y-2 text-[11px] sm:text-xs">
        {globalGates.map((g) => (
          <div key={g.name} className="flex items-center gap-1.5 min-w-0">
            <span
              className={`w-1.5 h-1.5 sm:w-2 sm:h-2 rounded-full shrink-0 ${
                g.ok ? "bg-green-400 animate-pulse" : "bg-red-500"
              }`}
            />
            <span className={`truncate ${g.ok ? "text-gray-200" : "text-gray-500"}`}>
              {g.name}
            </span>
          </div>
        ))}
      </div>

      {/* Per-bar strategy gates per watchlist symbol */}
      {perSymbol.length > 0 && (
        <div className="mt-3 sm:mt-4 pt-3 border-t border-gray-800">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-[10px] sm:text-xs font-semibold text-gray-500 uppercase tracking-wider">
              Per-Bar Gates · {perSymbol.length} symbol{perSymbol.length === 1 ? "" : "s"}
            </h3>
          </div>
          <div className="space-y-2">
            {perSymbol.map((rec) => {
              const allOk = PER_BAR_LABELS.every((g) => rec.gates[g.key]);
              return (
                <div key={rec.symbol} className="bg-gray-800/50 rounded-lg px-2 sm:px-3 py-2">
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      <span className="font-mono font-bold text-white text-xs sm:text-sm">{rec.symbol}</span>
                      {rec.setup && (
                        <span className="badge-gray text-[10px]">
                          {rec.setup.replace(/_/g, " ")}
                        </span>
                      )}
                      {rec.confidence && (
                        <span className={`text-[10px] font-mono font-bold ${
                          rec.confidence === "A" ? "text-green-400" : "text-yellow-400"
                        }`}>
                          {rec.confidence}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-gray-500 font-mono">
                      {allOk ? "✓ ALL PASS" : `${PER_BAR_LABELS.filter(g => !rec.gates[g.key]).length} blocked`}
                      <span className="ml-2">{relativeAge(rec.ts)}</span>
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-x-2 gap-y-1 text-[10px] sm:text-[11px]">
                    {PER_BAR_LABELS.map((g) => {
                      const ok = !!rec.gates[g.key];
                      return (
                        <div key={g.key} className="flex items-center gap-1.5 min-w-0">
                          <span
                            className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                              ok ? "bg-green-400 animate-pulse" : "bg-red-500"
                            }`}
                          />
                          <span className={`truncate ${ok ? "text-gray-200" : "text-gray-500"}`}>
                            {g.label}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
