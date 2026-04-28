/**
 * Live Alpaca portfolio passthrough.
 *
 * GET /api/portfolio/account   — equity, cash, buying power, day P&L
 * GET /api/portfolio/positions — open positions held at Alpaca
 * GET /api/portfolio/history   — portfolio equity time series (Alpaca-supplied)
 */

import { Router, Request, Response } from "express";

const router = Router();

function alpacaTradingBase(): string {
  const mode = (process.env.TRADING_MODE ?? "paper").toLowerCase();
  return mode === "live"
    ? "https://api.alpaca.markets"
    : "https://paper-api.alpaca.markets";
}

function alpacaHeaders() {
  return {
    "APCA-API-KEY-ID": process.env.ALPACA_API_KEY ?? "",
    "APCA-API-SECRET-KEY": process.env.ALPACA_SECRET_KEY ?? "",
  };
}

function credsMissing(): boolean {
  return !process.env.ALPACA_API_KEY || !process.env.ALPACA_SECRET_KEY;
}

router.get("/account", async (_req: Request, res: Response) => {
  if (credsMissing()) return res.status(503).json({ error: "Alpaca credentials missing" });
  try {
    const r = await fetch(`${alpacaTradingBase()}/v2/account`, { headers: alpacaHeaders() });
    if (!r.ok) {
      return res.status(r.status).json({ error: `Alpaca ${r.status}: ${await r.text()}` });
    }
    const a: any = await r.json();
    res.json({
      mode: (process.env.TRADING_MODE ?? "paper").toLowerCase(),
      equity: parseFloat(a.equity),
      lastEquity: parseFloat(a.last_equity),
      cash: parseFloat(a.cash),
      buyingPower: parseFloat(a.buying_power),
      portfolioValue: parseFloat(a.portfolio_value ?? a.equity),
      daytradeCount: parseInt(a.daytrade_count ?? "0", 10),
      dayPnl: parseFloat(a.equity) - parseFloat(a.last_equity),
      dayPnlPct: parseFloat(a.last_equity) > 0
        ? ((parseFloat(a.equity) - parseFloat(a.last_equity)) / parseFloat(a.last_equity)) * 100
        : 0,
      status: a.status,
      currency: a.currency,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.get("/positions", async (_req: Request, res: Response) => {
  if (credsMissing()) return res.status(503).json({ error: "Alpaca credentials missing" });
  try {
    const r = await fetch(`${alpacaTradingBase()}/v2/positions`, { headers: alpacaHeaders() });
    if (!r.ok) {
      return res.status(r.status).json({ error: `Alpaca ${r.status}: ${await r.text()}` });
    }
    const positions = (await r.json()) as any[];
    res.json(positions.map((p) => ({
      symbol: p.symbol,
      qty: parseInt(p.qty, 10),
      side: p.side,
      avgEntryPrice: parseFloat(p.avg_entry_price),
      currentPrice: parseFloat(p.current_price),
      marketValue: parseFloat(p.market_value),
      unrealizedPnl: parseFloat(p.unrealized_pl),
      unrealizedPnlPct: parseFloat(p.unrealized_plpc) * 100,
      changeToday: parseFloat(p.change_today) * 100,
    })));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/portfolio/history?period=1D&timeframe=5Min
//   period: 1D | 1W | 1M | 3M | 1A | all
//   timeframe: 1Min | 5Min | 15Min | 1H | 1D
router.get("/history", async (req: Request, res: Response) => {
  if (credsMissing()) return res.status(503).json({ error: "Alpaca credentials missing" });
  const period    = String(req.query.period ?? "1D");
  const timeframe = String(req.query.timeframe ?? "5Min");
  try {
    const url = new URL(`${alpacaTradingBase()}/v2/account/portfolio/history`);
    url.searchParams.set("period", period);
    url.searchParams.set("timeframe", timeframe);
    url.searchParams.set("extended_hours", "true");

    const r = await fetch(url.toString(), { headers: alpacaHeaders() });
    if (!r.ok) {
      return res.status(r.status).json({ error: `Alpaca ${r.status}: ${await r.text()}` });
    }
    const h: any = await r.json();
    // Alpaca returns parallel arrays: timestamp[], equity[], profit_loss[], profit_loss_pct[]
    const timestamps: number[] = h.timestamp ?? [];
    const equity: number[]     = h.equity ?? [];
    const pl: number[]         = h.profit_loss ?? [];
    const plPct: number[]      = h.profit_loss_pct ?? [];

    const points = timestamps.map((ts, i) => ({
      timestamp: new Date(ts * 1000).toISOString(),
      equity: equity[i],
      dayPnl: pl[i],
      pnlPct: (plPct[i] ?? 0) * 100,
    })).filter((p) => p.equity != null);

    res.json({
      period,
      timeframe,
      baseValue: h.base_value,
      points,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
