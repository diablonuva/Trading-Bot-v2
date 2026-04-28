import { Router } from "express";
import { prisma } from "../index";

const router = Router();

// GET /api/performance/summary — all-time stats
router.get("/summary", async (_req, res) => {
  try {
    const trades = await prisma.trade.findMany({
      where: { status: "CLOSED" },
      select: { realizedPnl: true, entryTime: true },
    });
    const winners = trades.filter((t) => (t.realizedPnl ?? 0) > 0);
    const losers = trades.filter((t) => (t.realizedPnl ?? 0) <= 0);
    const totalPnl = trades.reduce((s, t) => s + (t.realizedPnl ?? 0), 0);
    const grossWins = winners.reduce((s, t) => s + (t.realizedPnl ?? 0), 0);
    const grossLoss = Math.abs(losers.reduce((s, t) => s + (t.realizedPnl ?? 0), 0));

    res.json({
      totalTrades: trades.length,
      winningTrades: winners.length,
      losingTrades: losers.length,
      accuracyPct: trades.length ? (winners.length / trades.length) * 100 : 0,
      totalPnl: Math.round(totalPnl * 100) / 100,
      avgWinner: winners.length ? grossWins / winners.length : 0,
      avgLoser: losers.length ? -grossLoss / losers.length : 0,
      profitFactor: grossLoss > 0 ? grossWins / grossLoss : 0,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/performance/equity-curve — equity over time
router.get("/equity-curve", async (req, res) => {
  const { days = "30" } = req.query;
  try {
    const since = new Date();
    since.setDate(since.getDate() - parseInt(String(days)));
    const snaps = await prisma.equitySnapshot.findMany({
      where: { timestamp: { gte: since } },
      orderBy: { timestamp: "asc" },
      select: { equity: true, dayPnl: true, timestamp: true },
    });
    res.json(snaps);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/performance/daily — daily P&L series
router.get("/daily", async (req, res) => {
  const { days = "30" } = req.query;
  try {
    const since = new Date();
    since.setDate(since.getDate() - parseInt(String(days)));
    const sessions = await prisma.tradingSession.findMany({
      where: { date: { gte: since } },
      orderBy: { date: "asc" },
      select: { date: true, realizedPnl: true, totalTrades: true, accuracyPct: true, halted: true },
    });
    res.json(sessions);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
