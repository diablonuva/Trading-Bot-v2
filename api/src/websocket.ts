import { WebSocketServer, WebSocket } from "ws";
import { Request, Response, NextFunction } from "express";

export type WsEventType =
  | "trade_entry"
  | "trade_exit"
  | "signal"
  | "scan_complete"
  | "equity_update"
  | "position_update"
  | "bot_event"
  | "session_start"
  | "session_end"
  | "daily_halt"
  | "gate_check";

export function broadcast(wss: WebSocketServer, type: WsEventType, data: unknown) {
  const payload = JSON.stringify({ type, data, ts: Date.now() });
  wss.clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(payload);
    }
  });
}

// Injects req.broadcast() shortcut
export function broadcastMiddleware(wss: WebSocketServer) {
  return (req: Request, _res: Response, next: NextFunction) => {
    (req as any).broadcast = (type: WsEventType, data: unknown) =>
      broadcast(wss, type, data);
    next();
  };
}
