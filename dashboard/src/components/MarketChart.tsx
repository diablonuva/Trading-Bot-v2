import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
  CrosshairMode,
} from "lightweight-charts";

interface Bar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

interface BarsResponse {
  symbol: string;
  timeframe: string;
  bars: Bar[];
}

const TIMEFRAMES = ["1Min", "5Min", "15Min", "1Hour", "1Day"] as const;
type Timeframe = (typeof TIMEFRAMES)[number];

interface Props {
  defaultSymbol?: string;
  symbols?: string[];     // optional dropdown (e.g. SPY + watchlist + open positions)
  height?: number;
}

export default function MarketChart({
  defaultSymbol = "SPY",
  symbols = ["SPY", "QQQ", "IWM"],
  height = 360,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);

  const [symbol, setSymbol] = useState(defaultSymbol);
  const [timeframe, setTimeframe] = useState<Timeframe>("5Min");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // ---- chart init ----
  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      layout: { background: { color: "#0b1220" }, textColor: "#9ca3af" },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1f2937" },
      timeScale: { borderColor: "#1f2937", timeVisible: true, secondsVisible: false },
      width: containerRef.current.clientWidth,
      height,
    });
    chartRef.current = chart;

    candleRef.current = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      borderUpColor: "#22c55e",
      wickUpColor: "#22c55e",
      downColor: "#ef4444",
      borderDownColor: "#ef4444",
      wickDownColor: "#ef4444",
    });

    volumeRef.current = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      color: "#6b7280",
    });
    chart.priceScale("volume").applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });

    const ro = new ResizeObserver(() => {
      if (containerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: containerRef.current.clientWidth });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
    };
  }, [height]);

  // ---- fetch bars whenever symbol or timeframe changes ----
  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`/api/market/bars?symbol=${symbol}&timeframe=${timeframe}&limit=100`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: BarsResponse = await res.json();
        if (cancelled || !candleRef.current || !volumeRef.current) return;

        candleRef.current.setData(
          data.bars.map((b) => ({
            time: b.time as Time,
            open: b.open, high: b.high, low: b.low, close: b.close,
          }))
        );
        volumeRef.current.setData(
          data.bars.map((b) => ({
            time: b.time as Time,
            value: b.volume,
            color: b.close >= b.open ? "rgba(34,197,94,0.4)" : "rgba(239,68,68,0.4)",
          }))
        );
        chartRef.current?.timeScale().fitContent();
      } catch (e: any) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 30_000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [symbol, timeframe]);

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-gray-300">Market Chart</h2>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="bg-gray-800 border border-gray-700 text-white text-xs rounded px-2 py-1 font-mono"
          >
            {symbols.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div className="flex gap-1">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => setTimeframe(tf)}
              className={`text-xs px-2 py-1 rounded font-mono ${
                timeframe === tf
                  ? "bg-green-700 text-white"
                  : "bg-gray-800 text-gray-400 hover:bg-gray-700"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>
      <div ref={containerRef} className="w-full" style={{ height }} />
      {loading && <p className="text-xs text-gray-500 mt-2">Loading...</p>}
      {error && <p className="text-xs text-red-400 mt-2">Error: {error}</p>}
    </div>
  );
}
