import { Router } from "express";
import { prisma } from "../index";

const router = Router();

router.get("/", async (_req, res) => {
  try {
    const positions = await prisma.position.findMany({ orderBy: { entryTime: "asc" } });
    res.json(positions);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
