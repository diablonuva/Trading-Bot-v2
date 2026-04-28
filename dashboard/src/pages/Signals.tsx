import { useApi } from "../hooks/useApi";
import { format } from "date-fns";
import type { Signal } from "../types";

export default function SignalsPage() {
  const { data: signals } = useApi<Signal[]>("/api/signals?limit=100");

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Signal Log</h1>
      <p className="text-sm text-gray-500">
        Every signal evaluated by the strategy engine — both acted on and rejected.
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 text-xs uppercase tracking-wider border-b border-gray-800">
              <th className="py-2 px-3">Time</th>
              <th className="py-2 px-3">Symbol</th>
              <th className="py-2 px-3">Setup</th>
              <th className="py-2 px-3">Conf</th>
              <th className="py-2 px-3">R:R</th>
              <th className="py-2 px-3">Rvol</th>
              <th className="py-2 px-3">MACD</th>
              <th className="py-2 px-3">Acted</th>
              <th className="py-2 px-3">Reason</th>
            </tr>
          </thead>
          <tbody>
            {signals?.map((s) => (
              <tr key={s.id} className="border-b border-gray-800 hover:bg-gray-800/30">
                <td className="py-2 px-3 text-xs text-gray-500 font-mono">
                  {format(new Date(s.timestamp), "HH:mm:ss")}
                </td>
                <td className="py-2 px-3 font-mono font-bold">{s.symbol}</td>
                <td className="py-2 px-3">
                  <span className="badge-gray capitalize">{s.setup.replace(/_/g, " ")}</span>
                </td>
                <td className="py-2 px-3">
                  <span className={s.confidence === "A" ? "badge-green" : "badge-yellow"}>
                    {s.confidence}
                  </span>
                </td>
                <td className="py-2 px-3 font-mono text-gray-300">{s.rrRatio.toFixed(1)}</td>
                <td className="py-2 px-3 font-mono text-gray-300">
                  {s.rvolAtSignal ? `${s.rvolAtSignal.toFixed(1)}x` : "—"}
                </td>
                <td className="py-2 px-3 font-mono text-gray-300">
                  {s.macdLine != null ? s.macdLine.toFixed(3) : "—"}
                </td>
                <td className="py-2 px-3">
                  {s.acted
                    ? <span className="badge-green">Yes</span>
                    : <span className="badge-red">No</span>}
                </td>
                <td className="py-2 px-3 text-xs text-gray-500">
                  {s.rejectionReason ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
