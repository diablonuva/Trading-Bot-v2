import { useEffect, useState } from "react";

// NYSE full-closure holidays (regular hours = 09:30-16:00 ET).
// Dates are NY-local YYYY-MM-DD. Source: nyse.com/markets/hours-calendars.
// Extend yearly. Early-close days (Black Friday, Christmas Eve) aren't tracked
// — the countdown will say 16:00 on those days but actually it's 13:00.
const NYSE_HOLIDAYS = new Set<string>([
  // 2026
  "2026-01-01", // New Year's Day
  "2026-01-19", // MLK Day
  "2026-02-16", // Presidents' Day
  "2026-04-03", // Good Friday
  "2026-05-25", // Memorial Day
  "2026-06-19", // Juneteenth
  "2026-07-03", // Independence Day (observed; Jul 4 is Sat)
  "2026-09-07", // Labor Day
  "2026-11-26", // Thanksgiving
  "2026-12-25", // Christmas
  // 2027
  "2027-01-01",
  "2027-01-18", // MLK
  "2027-02-15", // Presidents'
  "2027-03-26", // Good Friday
  "2027-05-31", // Memorial
  "2027-06-18", // Juneteenth (observed; Jun 19 is Sat)
  "2027-07-05", // Independence Day (observed; Jul 4 is Sun)
  "2027-09-06", // Labor
  "2027-11-25", // Thanksgiving
  "2027-12-24", // Christmas (observed; Dec 25 is Sat)
  // 2028
  "2028-01-17", // MLK (Jan 1 is Sat — no observance)
  "2028-02-21", // Presidents'
  "2028-04-14", // Good Friday
  "2028-05-29", // Memorial
  "2028-06-19", // Juneteenth
  "2028-07-04", // Independence Day
  "2028-09-04", // Labor
  "2028-11-23", // Thanksgiving
  "2028-12-25", // Christmas
]);

function isHoliday(nyLocal: Date): boolean {
  const y = nyLocal.getFullYear();
  const m = String(nyLocal.getMonth() + 1).padStart(2, "0");
  const d = String(nyLocal.getDate()).padStart(2, "0");
  return NYSE_HOLIDAYS.has(`${y}-${m}-${d}`);
}

function isTradingDay(nyLocal: Date): boolean {
  const dow = nyLocal.getDay();
  return dow >= 1 && dow <= 5 && !isHoliday(nyLocal);
}

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

// NYSE regular session: Mon-Fri 09:30-16:00 ET, excluding holidays.
function nextMarketEvent(now: Date): MarketState {
  const ny = inZone("America/New_York", now);
  const tradingToday = isTradingDay(ny);

  const open = new Date(ny);  open.setHours(9, 30, 0, 0);
  const close = new Date(ny); close.setHours(16, 0, 0, 0);

  if (tradingToday && ny >= open && ny < close) {
    return { isOpen: true, msToEvent: close.getTime() - ny.getTime(), label: "Closes in" };
  }

  if (tradingToday && ny < open) {
    return { isOpen: false, msToEvent: open.getTime() - ny.getTime(), label: "Opens in" };
  }

  // After close OR weekend OR holiday — find next trading day at 09:30
  const next = new Date(ny);
  next.setHours(9, 30, 0, 0);
  do {
    next.setDate(next.getDate() + 1);
  } while (!isTradingDay(next));
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
    <div className="flex items-center gap-2 sm:gap-4 text-xs font-mono flex-wrap">
      <span className="text-gray-500 whitespace-nowrap">
        NY <span className="text-gray-200">{nyTime.slice(0, 5)}</span>
        <span className="hidden sm:inline text-gray-200">{nyTime.slice(5)}</span>
      </span>
      <span className="text-gray-500 whitespace-nowrap">
        SA <span className="text-gray-200">{saTime.slice(0, 5)}</span>
        <span className="hidden sm:inline text-gray-200">{saTime.slice(5)}</span>
      </span>
      <span
        className={`px-2 py-0.5 rounded border whitespace-nowrap ${
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
