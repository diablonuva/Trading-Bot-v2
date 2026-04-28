import { Router } from "express";
import { prisma } from "../index";

const router = Router();

router.get("/", async (req, res) => {
  const { date, acted, limit = "100" } = req.query;
  try {
    const where: any = {};
    if (acted !== undefined) where.acted = acted === "true";
    if (date) {
      const session = await prisma.tradingSession.findFirst({
        where: { date: new Date(String(date)) },
      });
      if (session) where.sessionId = session.id;
    }
    const signals = await prisma.signal.findMany({
      where,
      orderBy: { timestamp: "desc" },
      take: parseInt(String(limit)),
    });
    res.json(signals);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
