import { Router } from "express";
import { prisma } from "../index";

const router = Router();

// GET /api/trades — list trades (filter by date, symbol, status)
router.get("/", async (req, res) => {
  const { date, symbol, status, limit = "50", offset = "0" } = req.query;
  try {
    const where: any = {};
    if (symbol) where.symbol = String(symbol).toUpperCase();
    if (status) where.status = String(status).toUpperCase();
    if (date) {
      const session = await prisma.tradingSession.findFirst({
        where: { date: new Date(String(date)) },
      });
      if (session) where.sessionId = session.id;
    }
    const [trades, total] = await Promise.all([
      prisma.trade.findMany({
        where,
        orderBy: { entryTime: "desc" },
        take: parseInt(String(limit)),
        skip: parseInt(String(offset)),
      }),
      prisma.trade.count({ where }),
    ]);
    res.json({ trades, total });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/trades/today — open trades for today's session
router.get("/today", async (_req, res) => {
  try {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const session = await prisma.tradingSession.findFirst({ where: { date: today } });
    const trades = session
      ? await prisma.trade.findMany({
          where: { sessionId: session.id },
          orderBy: { entryTime: "asc" },
        })
      : [];
    res.json(trades);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/trades/:id
router.get("/:id", async (req, res) => {
  try {
    const trade = await prisma.trade.findUnique({ where: { id: req.params.id } });
    if (!trade) return res.status(404).json({ error: "Not found" });
    res.json(trade);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
