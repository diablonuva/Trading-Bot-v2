import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import http from "http";
import { WebSocketServer } from "ws";
import { PrismaClient } from "@prisma/client";

import tradesRouter from "./routes/trades";
import sessionsRouter from "./routes/sessions";
import signalsRouter from "./routes/signals";
import scannerRouter from "./routes/scanner";
import performanceRouter from "./routes/performance";
import eventsRouter from "./routes/events";
import positionsRouter from "./routes/positions";
import telemetryRouter from "./routes/telemetry";
import configRouter from "./routes/config";
import { broadcastMiddleware } from "./websocket";

const PORT = parseInt(process.env.PORT || "4000", 10);

export const prisma = new PrismaClient();

async function main() {
  const app = express();

  app.use(cors({ origin: "*" }));
  app.use(express.json({ limit: "2mb" }));

  // Rate limit: 100 requests per minute per IP
  app.use(rateLimit({ windowMs: 60_000, max: 100, standardHeaders: true, legacyHeaders: false }));

  // Health check (used by Docker healthcheck + nginx upstream check)
  app.get("/health", (_req, res) => res.json({ ok: true, ts: Date.now() }));

  // API routes
  app.use("/api/trades", tradesRouter);
  app.use("/api/sessions", sessionsRouter);
  app.use("/api/signals", signalsRouter);
  app.use("/api/scanner", scannerRouter);
  app.use("/api/performance", performanceRouter);
  app.use("/api/events", eventsRouter);
  app.use("/api/positions", positionsRouter);
  // Telemetry: Python bot POSTs here
  app.use("/telemetry", telemetryRouter);
  // Bot config (read settings.yaml + update trading mode)
  app.use("/api/config", configRouter);

  const server = http.createServer(app);

  // WebSocket for real-time dashboard updates
  const wss = new WebSocketServer({ server, path: "/ws" });
  app.locals.wss = wss;

  // Inject broadcast function into every request
  app.use(broadcastMiddleware(wss));

  wss.on("connection", (ws) => {
    console.log("Dashboard client connected");
    ws.on("close", () => console.log("Dashboard client disconnected"));
  });

  server.listen(PORT, "0.0.0.0", () => {
    console.log(`API server running on port ${PORT}`);
  });

  process.on("SIGTERM", async () => {
    await prisma.$disconnect();
    server.close();
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
