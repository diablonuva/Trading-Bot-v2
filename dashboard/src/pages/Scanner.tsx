import { useEffect } from "react";
import { useApi } from "../hooks/useApi";
import { useWebSocket } from "../hooks/useWebSocket";

interface ScanCandidate {
  symbol: string;
  price: number;
  pctChange: number;
  relativeVolume: number;
  floatShares?: number | null;
  hasNews: boolean;
  score: number;
  passedFilters: boolean;
  rank?: number;
}

interface ScanResult {
  scannedAt: string;
  candidatesFound: number;
  candidates: ScanCandidate[];
  universeSize?: number | null;
  evaluated?: number | null;
  rejectedPrice?: number | null;
  rejectedPct?: number | null;
  rejectedRvol?: number | null;
  rejectedFloat?: number | null;
  durationMs?: number | null;
}

interface BotConfig {
  scanner?: {
    price_min?: number;
    price_max?: number;
    relative_volume_min?: number;
    pct_change_min?: number;
    float_max_millions?: number;
    watchlist_size?: number;
    scan_interval_seconds?: number;
  };
}

function relativeAge(ts: string): string {
  const ms = Date.now() - new Date(ts).getTime();
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function formatFloat(shares?: number | null): string {
  if (shares == null) return "N/A";
  if (shares >= 1e9) return `${(shares / 1e9).toFixed(2)}B`;
  if (shares >= 1e6) return `${(shares / 1e6).toFixed(1)}M`;
  if (shares >= 1e3) return `${(shares / 1e3).toFixed(0)}K`;
  return String(shares);
}

export default function ScannerPage() {
  const { data: scanResult, refetch } = useApi<ScanResult>("/api/scanner/latest");
  const { data: config } = useApi<BotConfig>("/api/config");
  const { on } = useWebSocket();

  useEffect(() => {
    return on("scan_complete", () => refetch());
  }, [on, refetch]);

  const sc = config?.scanner;
  const totalRejected =
    (scanResult?.rejectedPrice ?? 0) +
    (scanResult?.rejectedPct ?? 0) +
    (scanResult?.rejectedRvol ?? 0) +
    (scanResult?.rejectedFloat ?? 0);

  return (
    <div className="space-y-4 sm:space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg sm:text-xl font-bold text-white">Scanner</h1>
        {scanResult?.scannedAt && (
          <span className="text-xs text-gray-500 font-mono">
            Last scan{" "}
            <span className="text-gray-200">
              {new Date(scanResult.scannedAt).toLocaleTimeString("en-GB", {
                timeZone: "America/New_York",
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
              })}{" "}
              ET
            </span>{" "}
            · {relativeAge(scanResult.scannedAt)}
          </span>
        )}
      </div>

      {/* Last-scan stats */}
      <div className="card">
        <h2 className="text-xs sm:text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          Last Scan
        </h2>
        {!scanResult ? (
          <p className="text-gray-600 text-sm">No scans yet today. Pre-market scan runs at 06:45 ET.</p>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4">
            <Stat label="Universe"   value={scanResult.universeSize?.toLocaleString() ?? "—"} hint="active US equities" />
            <Stat label="Evaluated"  value={scanResult.evaluated?.toLocaleString() ?? "—"}    hint="past price pre-filter" />
            <Stat label="Passed"     value={String(scanResult.candidatesFound)} color="green" hint="cleared all 5 pillars" />
            <Stat
              label="Took"
              value={
                scanResult.durationMs == null ? "—"
                  : scanResult.durationMs < 1000
                    ? `${scanResult.durationMs}ms`
                    : `${(scanResult.durationMs / 1000).toFixed(1)}s`
              }
              hint="round-trip"
            />
          </div>
        )}

        {/* Rejection breakdown */}
        {scanResult && totalRejected > 0 && (
          <div className="mt-4 pt-4 border-t border-gray-800">
            <p className="text-xs text-gray-500 uppercase tracking-wider mb-2">Rejected by Pillar</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs sm:text-sm">
              <RejectionStat label="Price out of range"   value={scanResult.rejectedPrice ?? 0} total={totalRejected} />
              <RejectionStat label="% change too low"      value={scanResult.rejectedPct ?? 0}   total={totalRejected} />
              <RejectionStat label="Relative vol too low"  value={scanResult.rejectedRvol ?? 0}  total={totalRejected} />
              <RejectionStat label="Float too large"       value={scanResult.rejectedFloat ?? 0} total={totalRejected} />
            </div>
          </div>
        )}
      </div>

      {/* Watchlist */}
      <div className="card">
        <h2 className="text-xs sm:text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3 flex items-center gap-2">
          Watchlist
          <span className="badge-yellow">{scanResult?.candidates.length ?? 0}</span>
        </h2>
        {!scanResult?.candidates.length ? (
          <p className="text-gray-600 text-sm">No candidates passed the 5-pillar filter on the last scan.</p>
        ) : (
          <div className="overflow-x-auto -mx-3 sm:mx-0">
            <table className="w-full text-xs sm:text-sm">
              <thead>
                <tr className="text-left text-gray-500 text-[10px] sm:text-xs uppercase tracking-wider border-b border-gray-800">
                  <th className="py-2 px-2 sm:px-3">#</th>
                  <th className="py-2 px-2 sm:px-3">Symbol</th>
                  <th className="py-2 px-2 sm:px-3">Price</th>
                  <th className="py-2 px-2 sm:px-3">% Change</th>
                  <th className="py-2 px-2 sm:px-3">Rel Vol</th>
                  <th className="py-2 px-2 sm:px-3 hidden sm:table-cell">Float</th>
                  <th className="py-2 px-2 sm:px-3 hidden sm:table-cell">News</th>
                  <th className="py-2 px-2 sm:px-3">Score</th>
                </tr>
              </thead>
              <tbody>
                {scanResult.candidates.map((c, i) => (
                  <tr key={c.symbol} className="border-b border-gray-800 hover:bg-gray-800/50">
                    <td className="py-2 px-2 sm:px-3 text-gray-500 font-mono">{c.rank ?? i + 1}</td>
                    <td className="py-2 px-2 sm:px-3 font-mono font-bold text-white">{c.symbol}</td>
                    <td className="py-2 px-2 sm:px-3 text-gray-200 font-mono">${c.price.toFixed(2)}</td>
                    <td className="py-2 px-2 sm:px-3 text-green-400 font-mono">+{c.pctChange.toFixed(1)}%</td>
                    <td className="py-2 px-2 sm:px-3 text-gray-300 font-mono">{c.relativeVolume.toFixed(1)}x</td>
                    <td className="py-2 px-2 sm:px-3 text-gray-300 font-mono hidden sm:table-cell">
                      {formatFloat(c.floatShares)}
                    </td>
                    <td className="py-2 px-2 sm:px-3 hidden sm:table-cell">
                      {c.hasNews ? <span className="badge-green">Yes</span> : <span className="badge-gray">No</span>}
                    </td>
                    <td className="py-2 px-2 sm:px-3 text-yellow-400 font-mono">{c.score.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Scanner config — the 5 pillars and current thresholds */}
      <div className="card">
        <h2 className="text-xs sm:text-sm font-semibold text-gray-300 uppercase tracking-wider mb-3">
          5-Pillar Filter Thresholds
        </h2>
        {!sc ? (
          <p className="text-gray-600 text-sm">Loading config...</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 sm:gap-3 text-xs sm:text-sm">
            <PillarRow
              n={1}
              name="Price range"
              value={`$${sc.price_min} – $${sc.price_max}`}
              note="Sweet spot for high-momentum small-caps"
            />
            <PillarRow
              n={2}
              name="Relative volume"
              value={`≥ ${sc.relative_volume_min}× 50-day avg`}
              note="Confirms unusual interest"
            />
            <PillarRow
              n={3}
              name="% change today"
              value={`≥ ${sc.pct_change_min}% up`}
              note="Already in motion before we scan"
            />
            <PillarRow
              n={4}
              name="Float"
              value={`< ${sc.float_max_millions}M shares`}
              note="Smaller float → bigger moves"
            />
            <PillarRow
              n={5}
              name="News catalyst"
              value="last 2h"
              note="Boosts score, doesn't block"
            />
            <PillarRow
              n={null}
              name="Watchlist size"
              value={`top ${sc.watchlist_size ?? "?"} by score`}
              note="Only these get bar-by-bar evaluation"
            />
          </div>
        )}
      </div>
    </div>
  );
}

// --- subcomponents ---

function Stat({
  label, value, hint, color,
}: { label: string; value: string; hint?: string; color?: "green" }) {
  return (
    <div>
      <p className="text-[10px] sm:text-xs text-gray-500 uppercase tracking-wider">{label}</p>
      <p className={`text-xl sm:text-2xl font-bold font-mono mt-1 ${color === "green" ? "text-green-400" : "text-white"}`}>
        {value}
      </p>
      {hint && <p className="text-[10px] sm:text-xs text-gray-600 mt-0.5">{hint}</p>}
    </div>
  );
}

function RejectionStat({ label, value, total }: { label: string; value: number; total: number }) {
  const pct = total > 0 ? (value / total) * 100 : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-gray-400 text-xs">{label}</span>
        <span className="font-mono text-red-400">{value.toLocaleString()}</span>
      </div>
      <div className="h-1 bg-gray-800 rounded-full overflow-hidden mt-1">
        <div className="h-full bg-red-500/60" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-gray-600">{pct.toFixed(1)}%</span>
    </div>
  );
}

function PillarRow({ n, name, value, note }: { n: number | null; name: string; value: string; note: string }) {
  return (
    <div className="flex gap-3 items-start py-1">
      <span className={`shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold font-mono ${
        n != null ? "bg-green-900/50 text-green-400 border border-green-800" : "bg-gray-800 text-gray-500 border border-gray-700"
      }`}>
        {n ?? "+"}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="text-gray-200 font-medium">{name}</span>
          <span className="font-mono text-yellow-400 text-xs sm:text-sm">{value}</span>
        </div>
        <p className="text-[10px] sm:text-xs text-gray-500">{note}</p>
      </div>
    </div>
  );
}
