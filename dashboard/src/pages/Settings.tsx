import { useState } from "react";
import { useApi } from "../hooks/useApi";
import type { TradingSession } from "../types";

type Config = Record<string, Record<string, unknown>>;

function SectionTable({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <div className="card">
      <h2 className="text-sm font-semibold text-gray-300 mb-3 uppercase tracking-wider">{title}</h2>
      <table className="w-full text-sm">
        <tbody>
          {Object.entries(data).map(([key, val]) => (
            <tr key={key} className="border-b border-gray-800 last:border-0">
              <td className="py-2 pr-4 text-gray-400 font-mono w-1/2">{key}</td>
              <td className="py-2 text-white font-mono">
                {typeof val === "boolean" ? (
                  <span className={val ? "text-green-400" : "text-red-400"}>
                    {String(val)}
                  </span>
                ) : (
                  String(val)
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SettingsPage() {
  const { data: config, loading: cfgLoading } = useApi<Config>("/api/config");
  const { data: session } = useApi<TradingSession>("/api/sessions/today");
  const [toggling, setToggling] = useState(false);
  const [modeMsg, setModeMsg] = useState<string | null>(null);

  const currentMode = session?.tradingMode ?? "paper";

  async function handleModeToggle() {
    const newMode = currentMode === "paper" ? "live" : "paper";
    if (
      newMode === "live" &&
      !window.confirm(
        "Switch to LIVE trading mode? This will use REAL MONEY on the next bot restart."
      )
    ) {
      return;
    }
    setToggling(true);
    setModeMsg(null);
    try {
      const res = await fetch("/api/config/trading-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: newMode }),
      });
      const data = await res.json();
      setModeMsg(data.note ?? `Mode updated to ${newMode}`);
    } catch {
      setModeMsg("Error updating mode — check API connection");
    } finally {
      setToggling(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Settings</h1>

      {/* Trading mode toggle */}
      <div className="card flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold text-gray-300">Trading Mode</p>
          <p className="text-xs text-gray-500 mt-1">
            Current session: <span className="font-mono text-white">{currentMode}</span>
          </p>
          {modeMsg && <p className="text-xs text-yellow-400 mt-2">{modeMsg}</p>}
        </div>
        <button
          onClick={handleModeToggle}
          disabled={toggling}
          className={`px-4 py-2 rounded-lg text-sm font-bold transition-colors disabled:opacity-50 ${
            currentMode === "paper"
              ? "bg-green-700 hover:bg-green-600 text-white"
              : "bg-red-700 hover:bg-red-600 text-white"
          }`}
        >
          {toggling
            ? "Updating..."
            : currentMode === "paper"
            ? "Switch to LIVE"
            : "Switch to PAPER"}
        </button>
      </div>

      {cfgLoading && <p className="text-gray-500 text-sm">Loading configuration...</p>}

      {config && (
        <>
          {config.trading     && <SectionTable title="Trading Schedule"   data={config.trading as Record<string, unknown>} />}
          {config.scanner     && <SectionTable title="Scanner Filters"    data={config.scanner as Record<string, unknown>} />}
          {config.risk        && <SectionTable title="Risk Management"    data={config.risk as Record<string, unknown>} />}
          {config.indicators  && <SectionTable title="Indicators"         data={config.indicators as Record<string, unknown>} />}
          {config.notifications && <SectionTable title="Notifications"    data={config.notifications as Record<string, unknown>} />}
        </>
      )}
    </div>
  );
}
