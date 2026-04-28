import { useState } from "react";
import { useApi } from "../hooks/useApi";
import TradeRow from "../components/TradeRow";
import type { Trade } from "../types";

export default function TradesPage() {
  const [status, setStatus] = useState("");
  const url = `/api/trades?limit=100${status ? `&status=${status}` : ""}`;
  const { data, loading } = useApi<{ trades: Trade[]; total: number }>(url, [status]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Trade History</h1>
        <div className="flex gap-2">
          {["", "CLOSED", "OPEN"].map((s) => (
            <button
              key={s}
              onClick={() => setStatus(s)}
              className={`px-3 py-1 rounded text-sm font-medium transition-colors ${
                status === s
                  ? "bg-green-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {s || "All"}
            </button>
          ))}
        </div>
      </div>

      <div className="card overflow-x-auto">
        {loading ? (
          <p className="text-gray-500 text-sm py-4">Loading...</p>
        ) : (
          <>
            <p className="text-xs text-gray-600 mb-3">{data?.total ?? 0} total trades</p>
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
                {data?.trades.map((t) => <TradeRow key={t.id} trade={t} />)}
              </tbody>
            </table>
          </>
        )}
      </div>
    </div>
  );
}
