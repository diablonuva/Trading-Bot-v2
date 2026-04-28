import { Router } from "express";
import { prisma } from "../index";

const router = Router();

router.get("/", async (req, res) => {
  const { severity, limit = "100", sessionId } = req.query;
  try {
    const where: any = {};
    if (severity) where.severity = String(severity).toUpperCase();
    if (sessionId) where.sessionId = String(sessionId);
    const events = await prisma.botEvent.findMany({
      where,
      orderBy: { timestamp: "desc" },
      take: parseInt(String(limit)),
    });
    res.json(events);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
