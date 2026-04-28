interface Props {
  label: string;
  value: string | number;
  sub?: string;
  color?: "green" | "red" | "yellow" | "default";
}

export default function StatCard({ label, value, sub, color = "default" }: Props) {
  const valueColor = {
    green: "text-green-400",
    red: "text-red-400",
    yellow: "text-yellow-400",
    default: "text-white",
  }[color];

  return (
    <div className="card flex flex-col gap-1">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${valueColor}`}>{value}</span>
      {sub && <span className="text-xs text-gray-500">{sub}</span>}
    </div>
  );
}
