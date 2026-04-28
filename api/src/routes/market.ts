/**
 * Market data passthrough — fetches OHLCV bars from Alpaca for the dashboard.
 *
 * GET /api/market/bars?symbol=SPY&timeframe=5Min&limit=100
 *   timeframe: 1Min | 5Min | 15Min | 1Hour | 1Day
 *   limit: 1..1000 (default 100)
 */

import { Router, Request, Response } from "express";

const router = Router();

const ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks/bars";

const VALID_TIMEFRAMES = new Set(["1Min", "5Min", "15Min", "1Hour", "1Day"]);

router.get("/bars", async (req: Request, res: Response) => {
  const symbol = String(req.query.symbol ?? "SPY").toUpperCase();
  const timeframe = String(req.query.timeframe ?? "5Min");
  const limit = Math.min(Math.max(parseInt(String(req.query.limit ?? "100"), 10) || 100, 1), 1000);

  if (!VALID_TIMEFRAMES.has(timeframe)) {
    return res.status(400).json({ error: `Invalid timeframe. One of: ${[...VALID_TIMEFRAMES].join(", ")}` });
  }

  const apiKey = process.env.ALPACA_API_KEY;
  const apiSecret = process.env.ALPACA_SECRET_KEY;
  if (!apiKey || !apiSecret) {
    return res.status(503).json({ error: "Alpaca credentials missing on server" });
  }

  // Pull a window large enough that 'limit' bars actually come back.
  // 1Min bars need ~limit minutes; 5Min bars need ~5x more wall clock; etc.
  const minutesPerBar: Record<string, number> = {
    "1Min": 1, "5Min": 5, "15Min": 15, "1Hour": 60, "1Day": 60 * 24,
  };
  const lookbackMs = minutesPerBar[timeframe] * limit * 60_000 * 3; // 3x for weekends/holidays
  const start = new Date(Date.now() - lookbackMs).toISOString();

  const url = new URL(ALPACA_DATA_URL);
  url.searchParams.set("symbols", symbol);
  url.searchParams.set("timeframe", timeframe);
  url.searchParams.set("limit", String(limit));
  url.searchParams.set("start", start);
  url.searchParams.set("adjustment", "raw");
  url.searchParams.set("feed", "iex");  // free tier; switch to "sip" if you have the entitlement

  try {
    const r = await fetch(url.toString(), {
      headers: {
        "APCA-API-KEY-ID": apiKey,
        "APCA-API-SECRET-KEY": apiSecret,
      },
    });

    if (!r.ok) {
      const text = await r.text();
      return res.status(r.status).json({ error: `Alpaca ${r.status}: ${text}` });
    }

    const data: any = await r.json();
    const bars = (data?.bars?.[symbol] ?? []) as any[];

    // Convert to lightweight-charts shape: { time, open, high, low, close, volume }
    const out = bars.map((b) => ({
      time: Math.floor(new Date(b.t).getTime() / 1000),  // unix seconds
      open: b.o,
      high: b.h,
      low: b.l,
      close: b.c,
      volume: b.v,
    }));

    res.json({ symbol, timeframe, bars: out });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
