import { useEffect, useState } from "react";

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

// Compute current ET hour/minute for the trading-window check
function nyHourMinute(): { hour: number; minute: number; weekday: number } {
  const ny = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
  return { hour: ny.getHours(), minute: ny.getMinutes(), weekday: ny.getDay() };
}

export default function TradeEntryGates({
  session,
  account,
  watchlistCount,
  wsConnected,
  maxTradesPerDay = 10,
}: Props) {
  // Tick every 30s so the trading-window gate flips at 07:00 / 11:00 ET
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000);
    return () => clearInterval(id);
  }, []);

  const { hour, minute, weekday } = nyHourMinute();
  const isWeekday = weekday >= 1 && weekday <= 5;
  const inEntryWindow = isWeekday
    && (hour > 7 || (hour === 7 && minute >= 0))
    && hour < 11;
  const inMarketHours = isWeekday
    && (hour > 9 || (hour === 9 && minute >= 30))
    && hour < 16;

  const tradesUsed = session?.totalTrades ?? 0;
  const buyingPower = account?.buyingPower ?? 0;
  const dayTrades = account?.daytradeCount ?? 0;
  const sessionActive = !!session && !session.endedAt;

  const gates: Gate[] = [
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

  const blocked = gates.filter((g) => !g.ok).length;
  const total = gates.length;

  return (
    <div className="card bg-gray-900/80 border-gray-800">
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
      <div className="grid grid-cols-3 gap-x-2 sm:gap-x-4 gap-y-1.5 sm:gap-y-2 text-[11px] sm:text-xs">
        {gates.map((g) => (
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
    </div>
  );
}
