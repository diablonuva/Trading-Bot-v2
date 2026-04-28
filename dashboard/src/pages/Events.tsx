import { useApi } from "../hooks/useApi";
import { useWebSocket } from "../hooks/useWebSocket";
import { useEffect, useState } from "react";
import { format } from "date-fns";
import type { BotEvent } from "../types";

const SEVERITY_CLASS: Record<string, string> = {
  DEBUG:    "text-gray-500",
  INFO:     "text-blue-400",
  WARNING:  "text-yellow-400",
  ERROR:    "text-red-400",
  CRITICAL: "text-red-300 font-bold",
};

export default function EventsPage() {
  const { data: initial } = useApi<BotEvent[]>("/api/events?limit=200");
  const [events, setEvents] = useState<BotEvent[]>([]);
  const { on } = useWebSocket();

  useEffect(() => {
    if (initial) setEvents(initial);
  }, [initial]);

  useEffect(() => {
    return on("bot_event", (data) => {
      setEvents((prev) => [data as BotEvent, ...prev].slice(0, 500));
    });
  }, [on]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Bot Event Log</h1>
      <div className="card font-mono text-xs space-y-1 max-h-[75vh] overflow-y-auto">
        {events.map((e) => (
          <div key={e.id} className="flex gap-3 items-start py-1 border-b border-gray-800/50">
            <span className="text-gray-600 shrink-0">
              {format(new Date(e.timestamp), "HH:mm:ss")}
            </span>
            <span className={`${SEVERITY_CLASS[e.severity] ?? "text-gray-400"} shrink-0 w-16`}>
              {e.severity}
            </span>
            <span className="text-gray-500 shrink-0 w-36">{e.eventType}</span>
            <span className="text-gray-300">{e.message}</span>
          </div>
        ))}
        {!events.length && (
          <p className="text-gray-600 py-4">No events yet</p>
        )}
      </div>
    </div>
  );
}
