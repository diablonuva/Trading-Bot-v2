/**
 * GET /api/gates/latest — most recent per-bar gate evaluations from the bot's
 * strategy, one record per symbol, sorted newest first. Backed by the
 * in-memory cache populated by POST /telemetry/gate-check.
 */

import { Router, Request, Response } from "express";
import { getGateCache } from "./telemetry";

const router = Router();

router.get("/latest", (_req: Request, res: Response) => {
  res.json({ records: getGateCache() });
});

export default router;
