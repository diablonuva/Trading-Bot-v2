/**
 * Telemetry ingestion endpoint — called by the Python bot.
 *
 * The bot POSTs structured events here. Each event is persisted to
 * PostgreSQL and broadcast to all connected WebSocket clients in real time.
 *
 * POST /telemetry/event        — generic bot event
 * POST /telemetry/trade/entry  — new trade opened
 * POST /telemetry/trade/exit   — trade closed
 * POST /telemetry/signal       — signal evaluated (acted on or not)
 * POST /telemetry/scan         — scanner result
 * POST /telemetry/equity       — equity snapshot
 * POST /telemetry/position     — open position update
 * POST /telemetry/session/start
 * POST /telemetry/session/end
 */

import { Router, Request, Response } from "express";
import { prisma } from "../index";
import { broadcast } from "../websocket";

const router = Router();

// Per-symbol latest gate-check, kept in process memory only. Cheap and high
// frequency (one per bar per watchlist symbol), so persisting would just
// bloat Postgres. Cleared every 30 min so stale entries don't pile up.
interface GateCheckRecord {
  symbol: string;
  gates: Record<string, boolean>;
  setup: string | null;
  confidence: string | null;
  ts: string;
  receivedAt: number;
}
const gateCache = new Map<string, GateCheckRecord>();
setInterval(() => {
  const cutoff = Date.now() - 30 * 60 * 1000;
  for (const [sym, rec] of gateCache.entries()) {
    if (rec.receivedAt < cutoff) gateCache.delete(sym);
  }
}, 60 * 1000);
export function getGateCache(): GateCheckRecord[] {
  return [...gateCache.values()].sort((a, b) => b.receivedAt - a.receivedAt);
}

// ----------------------------------------------------------------
// Session start
// ----------------------------------------------------------------
router.post("/session/start", async (req: Request, res: Response) => {
  const { date, startingEquity, tradingMode } = req.body;
  try {
    const session = await prisma.tradingSession.upsert({
      where: { date: new Date(date) },
      update: { startedAt: new Date(), startingEquity, tradingMode },
      create: { date: new Date(date), startingEquity, tradingMode },
    });
    broadcast(req.app.locals.wss, "session_start", session);
    res.json(session);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Session end
// ----------------------------------------------------------------
router.post("/session/end", async (req: Request, res: Response) => {
  const { date, endingEquity, realizedPnl, totalTrades, winningTrades,
          losingTrades, accuracyPct, avgWinner, avgLoser, halted, haltReason } = req.body;
  try {
    const session = await prisma.tradingSession.update({
      where: { date: new Date(date) },
      data: { endedAt: new Date(), endingEquity, realizedPnl, totalTrades,
               winningTrades, losingTrades, accuracyPct, avgWinner, avgLoser,
               halted, haltReason },
    });
    broadcast(req.app.locals.wss, "session_end", session);
    res.json(session);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Trade entry
// ----------------------------------------------------------------
router.post("/trade/entry", async (req: Request, res: Response) => {
  const { sessionDate, symbol, setup, qty, entryPrice, stopPrice, targetPrice,
          entryOrderId, entryVwap, entryMacd, entryRvol, signalId } = req.body;
  try {
    const session = await prisma.tradingSession.findUnique({
      where: { date: new Date(sessionDate) },
    });
    if (!session) return res.status(404).json({ error: "Session not found" });

    const riskPerShare = entryPrice - stopPrice;
    const rewardPerShare = targetPrice - entryPrice;
    const trade = await prisma.trade.create({
      data: {
        sessionId: session.id,
        symbol, setup, qty,
        entryPrice, stopPrice, targetPrice,
        riskPerShare, rewardPerShare,
        rrRatio: riskPerShare > 0 ? rewardPerShare / riskPerShare : 0,
        entryTime: new Date(),
        entryOrderId, entryVwap, entryMacd, entryRvol,
        signalId: signalId || null,
        status: "OPEN",
      },
    });

    // Update open position record
    await prisma.position.upsert({
      where: { symbol },
      update: { qty, entryPrice, stopPrice, targetPrice, setup, entryTime: new Date() },
      create: { symbol, qty, entryPrice, stopPrice, targetPrice, setup, entryTime: new Date() },
    });

    broadcast(req.app.locals.wss, "trade_entry", trade);
    res.json(trade);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Trade exit
// ----------------------------------------------------------------
router.post("/trade/exit", async (req: Request, res: Response) => {
  const { tradeId, exitPrice, exitReason, exitOrderId } = req.body;
  try {
    const existing = await prisma.trade.findUnique({ where: { id: tradeId } });
    if (!existing) return res.status(404).json({ error: "Trade not found" });

    const realizedPnl = (exitPrice - existing.entryPrice) * existing.qty;
    const pnlPct = ((exitPrice - existing.entryPrice) / existing.entryPrice) * 100;
    const holdMs = Date.now() - existing.entryTime.getTime();
    const holdMinutes = Math.round(holdMs / 60000);

    const trade = await prisma.trade.update({
      where: { id: tradeId },
      data: { exitPrice, exitTime: new Date(), exitReason, exitOrderId,
               realizedPnl, pnlPct, holdMinutes, status: "CLOSED" },
    });

    // Remove from open positions
    await prisma.position.deleteMany({ where: { symbol: existing.symbol } });

    broadcast(req.app.locals.wss, "trade_exit", trade);
    res.json(trade);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Signal evaluated
// ----------------------------------------------------------------
router.post("/signal", async (req: Request, res: Response) => {
  const { sessionDate, symbol, setup, confidence, entryPrice, stopPrice,
          targetPrice, acted, rejectionReason, vwap, macdLine, macdHistogram,
          rvolAtSignal, price, pctChange } = req.body;
  try {
    const session = await prisma.tradingSession.findUnique({
      where: { date: new Date(sessionDate) },
    });
    if (!session) return res.status(404).json({ error: "Session not found" });

    const rrRatio = stopPrice < entryPrice
      ? (targetPrice - entryPrice) / (entryPrice - stopPrice) : 0;

    const signal = await prisma.signal.create({
      data: {
        sessionId: session.id,
        symbol, setup, confidence,
        entryPrice, stopPrice, targetPrice, rrRatio,
        acted, rejectionReason,
        vwap, macdLine, macdHistogram, rvolAtSignal, price, pctChange,
      },
    });
    broadcast(req.app.locals.wss, "signal", signal);
    res.json(signal);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Scanner result
// ----------------------------------------------------------------
router.post("/scan", async (req: Request, res: Response) => {
  const { sessionDate, candidates, stats } = req.body;
  try {
    const session = await prisma.tradingSession.findUnique({
      where: { date: new Date(sessionDate) },
    });
    if (!session) return res.status(404).json({ error: "Session not found" });

    const passed = candidates.filter((c: any) => c.passedFilters);
    const s = stats ?? {};
    const scanResult = await prisma.scanResult.create({
      data: {
        sessionId: session.id,
        candidatesFound: passed.length,
        universeSize:  s.universeSize  ?? null,
        evaluated:     s.evaluated     ?? null,
        rejectedPrice: s.rejectedPrice ?? null,
        rejectedPct:   s.rejectedPct   ?? null,
        rejectedRvol:  s.rejectedRvol  ?? null,
        rejectedFloat: s.rejectedFloat ?? null,
        durationMs:    s.durationMs    ?? null,
        candidates: {
          create: candidates.map((c: any, i: number) => ({ ...c, rank: i + 1 })),
        },
      },
      include: { candidates: true },
    });

    // Upsert WatchlistEntry for each candidate that passed filters
    for (const c of passed) {
      await prisma.watchlistEntry.create({
        data: {
          sessionId: session.id,
          symbol: c.symbol,
          addReason: "periodic_scan",
          score: c.score ?? null,
        },
      });
    }

    broadcast(req.app.locals.wss, "scan_complete", scanResult);
    res.json(scanResult);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Equity snapshot
// ----------------------------------------------------------------
router.post("/equity", async (req: Request, res: Response) => {
  const { sessionDate, equity, buyingPower, dayPnl, openPositionCount } = req.body;
  try {
    const session = await prisma.tradingSession.findFirst({
      where: { date: new Date(sessionDate) },
    });
    const snap = await prisma.equitySnapshot.create({
      data: { sessionId: session?.id, equity, buyingPower, dayPnl, openPositionCount },
    });
    broadcast(req.app.locals.wss, "equity_update", snap);
    res.json(snap);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Position update (live unrealized PnL)
// ----------------------------------------------------------------
router.post("/position", async (req: Request, res: Response) => {
  const { symbol, currentPrice, unrealizedPnl } = req.body;
  try {
    const pos = await prisma.position.updateMany({
      where: { symbol },
      data: { currentPrice, unrealizedPnl },
    });
    broadcast(req.app.locals.wss, "position_update", { symbol, currentPrice, unrealizedPnl });
    res.json({ updated: pos.count });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ----------------------------------------------------------------
// Per-bar gate-check from the strategy. Stored in-memory only and
// broadcast over WebSocket so the dashboard's TradeEntryGates panel can
// flip per-symbol pulses live.
// ----------------------------------------------------------------
router.post("/gate-check", async (req: Request, res: Response) => {
  const { symbol, gates, setup, confidence, ts } = req.body;
  if (!symbol || !gates) return res.status(400).json({ error: "symbol and gates required" });
  const record: GateCheckRecord = {
    symbol,
    gates,
    setup: setup ?? null,
    confidence: confidence ?? null,
    ts: ts ?? new Date().toISOString(),
    receivedAt: Date.now(),
  };
  gateCache.set(symbol, record);
  broadcast(req.app.locals.wss, "gate_check", record);
  res.json({ ok: true });
});

// ----------------------------------------------------------------
// Generic bot event
// ----------------------------------------------------------------
router.post("/event", async (req: Request, res: Response) => {
  const { sessionDate, eventType, severity, message, metadata } = req.body;
  try {
    const session = sessionDate
      ? await prisma.tradingSession.findFirst({ where: { date: new Date(sessionDate) } })
      : null;
    const event = await prisma.botEvent.create({
      data: { sessionId: session?.id, eventType, severity: severity || "INFO", message, metadata },
    });
    broadcast(req.app.locals.wss, "bot_event", event);
    res.json(event);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
