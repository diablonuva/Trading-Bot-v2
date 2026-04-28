/**
 * GET  /api/config          — read config/settings.yaml as JSON
 * POST /api/config/trading-mode — write TRADING_MODE to .env + log a bot event
 */

import { Router, Request, Response } from "express";
import fs from "fs";
import path from "path";
import { parse as parseYaml } from "yaml";
import { prisma } from "../index";
import { broadcast } from "../websocket";

const router = Router();

// Resolve config/settings.yaml relative to project root (two levels up from dist/)
const SETTINGS_PATH = path.resolve(process.cwd(), "../bot/config/settings.yaml");
const ENV_PATH      = path.resolve(process.cwd(), "../.env");

router.get("/", (_req: Request, res: Response) => {
  try {
    const raw = fs.readFileSync(SETTINGS_PATH, "utf8");
    const cfg = parseYaml(raw);
    res.json(cfg);
  } catch (e: any) {
    res.status(500).json({ error: `Could not read settings.yaml: ${e.message}` });
  }
});

router.post("/trading-mode", async (req: Request, res: Response) => {
  const { mode } = req.body;
  if (!mode || !["paper", "live"].includes(mode)) {
    return res.status(400).json({ error: "mode must be 'paper' or 'live'" });
  }

  try {
    // Rewrite TRADING_MODE in .env
    let envContent = "";
    if (fs.existsSync(ENV_PATH)) {
      envContent = fs.readFileSync(ENV_PATH, "utf8");
    }

    if (envContent.includes("TRADING_MODE=")) {
      envContent = envContent.replace(/^TRADING_MODE=.*/m, `TRADING_MODE=${mode}`);
    } else {
      envContent += `\nTRADING_MODE=${mode}\n`;
    }
    fs.writeFileSync(ENV_PATH, envContent, "utf8");

    // Log a bot event so the dashboard shows the change
    const event = await prisma.botEvent.create({
      data: {
        eventType: "INFO",
        severity: "INFO",
        message: `Trading mode changed to ${mode.toUpperCase()} via dashboard. Restart bot to apply.`,
        metadata: { mode },
      },
    });
    broadcast(req.app.locals.wss, "bot_event", event);

    res.json({ ok: true, mode, note: "Restart the bot container to apply the new mode." });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
