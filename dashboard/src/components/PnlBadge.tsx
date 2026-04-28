interface Props {
  value: number;
  prefix?: string;
  suffix?: string;
}

export default function PnlBadge({ value, prefix = "$", suffix = "" }: Props) {
  const formatted = `${value >= 0 ? "+" : ""}${prefix}${value.toFixed(2)}${suffix}`;
  const cls = value > 0 ? "pnl-positive" : value < 0 ? "pnl-negative" : "pnl-neutral";
  return <span className={`font-mono font-semibold ${cls}`}>{formatted}</span>;
}
