import { Router } from "express";
import { prisma } from "../index";

const router = Router();

// GET /api/sessions — list sessions
router.get("/", async (req, res) => {
  const { limit = "30" } = req.query;
  try {
    const sessions = await prisma.tradingSession.findMany({
      orderBy: { date: "desc" },
      take: parseInt(String(limit)),
      include: { _count: { select: { trades: true } } },
    });
    res.json(sessions);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/sessions/today
router.get("/today", async (_req, res) => {
  try {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const session = await prisma.tradingSession.findFirst({
      where: { date: today },
      include: {
        trades: { orderBy: { entryTime: "asc" } },
        _count: { select: { trades: true, signals: true } },
      },
    });
    res.json(session);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /api/sessions/:id
router.get("/:id", async (req, res) => {
  try {
    const session = await prisma.tradingSession.findUnique({
      where: { id: req.params.id },
      include: {
        trades: true,
        signals: true,
        botEvents: { orderBy: { timestamp: "desc" }, take: 50 },
        equitySnapshots: { orderBy: { timestamp: "asc" } },
      },
    });
    if (!session) return res.status(404).json({ error: "Not found" });
    res.json(session);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
