import { useEffect, useRef, useCallback, useState } from "react";
import type { WsMessage, WsEventType } from "../types";

type Handler = (data: unknown) => void;

export function useWebSocket() {
  const ws = useRef<WebSocket | null>(null);
  const handlers = useRef<Partial<Record<WsEventType, Handler[]>>>({});
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${protocol}://${window.location.host}/ws`;
    const socket = new WebSocket(url);

    socket.onopen = () => {
      setConnected(true);
      console.log("WS connected");
    };

    socket.onclose = () => {
      setConnected(false);
      // Reconnect after 3 seconds
      setTimeout(connect, 3000);
    };

    socket.onmessage = (event) => {
      try {
        const msg: WsMessage = JSON.parse(event.data);
        const typeHandlers = handlers.current[msg.type] ?? [];
        typeHandlers.forEach((h) => h(msg.data));
      } catch {
        // ignore malformed messages
      }
    };

    ws.current = socket;
  }, []);

  useEffect(() => {
    connect();
    return () => ws.current?.close();
  }, [connect]);

  const on = useCallback((type: WsEventType, handler: Handler) => {
    if (!handlers.current[type]) handlers.current[type] = [];
    handlers.current[type]!.push(handler);
    return () => {
      handlers.current[type] = handlers.current[type]!.filter((h) => h !== handler);
    };
  }, []);

  return { connected, on };
}
