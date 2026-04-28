import { format } from "date-fns";
import type { Trade } from "../types";
import PnlBadge from "./PnlBadge";

interface Props { trade: Trade; }

export default function TradeRow({ trade }: Props) {
  const setup = trade.setup.replace(/_/g, " ");

  return (
    <tr className="border-b border-gray-800 hover:bg-gray-800/40 transition-colors">
      <td className="py-2 px-3 font-mono font-bold text-white">{trade.symbol}</td>
      <td className="py-2 px-3">
        <span className="badge-gray capitalize">{setup}</span>
      </td>
      <td className="py-2 px-3 font-mono text-gray-300">{trade.qty}</td>
      <td className="py-2 px-3 font-mono text-gray-300">${trade.entryPrice.toFixed(2)}</td>
      <td className="py-2 px-3 font-mono text-gray-300">
        {trade.exitPrice ? `$${trade.exitPrice.toFixed(2)}` : "—"}
      </td>
      <td className="py-2 px-3">
        {trade.realizedPnl != null
          ? <PnlBadge value={trade.realizedPnl} />
          : <span className="text-gray-500">open</span>}
      </td>
      <td className="py-2 px-3 font-mono text-gray-500 text-xs">
        {trade.holdMinutes != null ? `${trade.holdMinutes}m` : "—"}
      </td>
      <td className="py-2 px-3">
        {trade.status === "OPEN"
          ? <span className="badge-yellow">Open</span>
          : trade.realizedPnl != null && trade.realizedPnl > 0
            ? <span className="badge-green">Win</span>
            : <span className="badge-red">Loss</span>}
      </td>
      <td className="py-2 px-3 text-xs text-gray-600">
        {format(new Date(trade.entryTime), "HH:mm:ss")}
      </td>
    </tr>
  );
}
