import { Router } from "express";
import { prisma } from "../index";

const router = Router();

// GET /api/scanner/latest — most recent scan result with candidates
router.get("/latest", async (_req, res) => {
  try {
    const result = await prisma.scanResult.findFirst({
      orderBy: { scannedAt: "desc" },
      include: { candidates: { orderBy: { rank: "asc" } } },
    });
    res.json(result);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.get("/history", async (req, res) => {
  const { limit = "20" } = req.query;
  try {
    const results = await prisma.scanResult.findMany({
      orderBy: { scannedAt: "desc" },
      take: parseInt(String(limit)),
      include: { candidates: { where: { passedFilters: true }, orderBy: { rank: "asc" } } },
    });
    res.json(results);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
