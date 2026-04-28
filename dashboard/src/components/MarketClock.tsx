import { useEffect, useState } from "react";

// Returns a Date whose getHours()/getMinutes()/getDay() reflect wall-clock time
// in the given IANA timezone. Used so we can do simple arithmetic on NY-local time.
function inZone(zone: string, now: Date): Date {
  return new Date(now.toLocaleString("en-US", { timeZone: zone }));
}

function formatTime(zone: string, now: Date): string {
  return now.toLocaleTimeString("en-GB", {
    timeZone: zone,
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

interface MarketState {
  isOpen: boolean;
  msToEvent: number;
  label: string;  // "Closes in" | "Opens in"
}

// NYSE regular session: Mon–Fri 09:30–16:00 ET. Holidays not handled (rare,
// the worst case is a slightly stale label one day per quarter).
function nextMarketEvent(now: Date): MarketState {
  const ny = inZone("America/New_York", now);
  const dow = ny.getDay();              // 0 = Sun, 6 = Sat
  const isWeekday = dow >= 1 && dow <= 5;

  const open = new Date(ny);  open.setHours(9, 30, 0, 0);
  const close = new Date(ny); close.setHours(16, 0, 0, 0);

  if (isWeekday && ny >= open && ny < close) {
    return { isOpen: true, msToEvent: close.getTime() - ny.getTime(), label: "Closes in" };
  }

  if (isWeekday && ny < open) {
    return { isOpen: false, msToEvent: open.getTime() - ny.getTime(), label: "Opens in" };
  }

  // After close OR weekend — find next weekday's 09:30
  const next = new Date(ny);
  next.setHours(9, 30, 0, 0);
  do {
    next.setDate(next.getDate() + 1);
  } while (next.getDay() === 0 || next.getDay() === 6);
  return { isOpen: false, msToEvent: next.getTime() - ny.getTime(), label: "Opens in" };
}

function formatCountdown(ms: number): string {
  if (ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h >= 24) {
    const d = Math.floor(h / 24);
    return `${d}d ${h % 24}h ${m.toString().padStart(2, "0")}m`;
  }
  return `${h}h ${m.toString().padStart(2, "0")}m ${s.toString().padStart(2, "0")}s`;
}

export default function MarketClock() {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const nyTime = formatTime("America/New_York", now);
  const saTime = formatTime("Africa/Johannesburg", now);
  const { isOpen, msToEvent, label } = nextMarketEvent(now);

  return (
    <div className="flex items-center gap-4 text-xs font-mono">
      <span className="text-gray-500">
        NY <span className="text-gray-200">{nyTime}</span>
      </span>
      <span className="text-gray-500">
        SA <span className="text-gray-200">{saTime}</span>
      </span>
      <span
        className={`px-2 py-0.5 rounded border ${
          isOpen
            ? "border-green-800 bg-green-900/30 text-green-400"
            : "border-yellow-800 bg-yellow-900/20 text-yellow-400"
        }`}
        title={isOpen ? "NYSE regular session is open" : "NYSE is closed"}
      >
        {label} {formatCountdown(msToEvent)}
      </span>
    </div>
  );
}
