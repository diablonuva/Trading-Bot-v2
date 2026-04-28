import { Routes, Route, NavLink } from "react-router-dom";
import { useWebSocket } from "./hooks/useWebSocket";
import DashboardPage from "./pages/Dashboard";
import TradesPage from "./pages/Trades";
import AnalyticsPage from "./pages/Analytics";
import SignalsPage from "./pages/Signals";
import EventsPage from "./pages/Events";
import SettingsPage from "./pages/Settings";

export default function App() {
  const { connected } = useWebSocket();

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top nav */}
      <nav className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-6">
        <div className="flex items-center gap-2 mr-6">
          <span className="text-xl font-bold text-green-400 font-mono">⚡ TradingBot</span>
          <span className="text-gray-600 text-sm">v2</span>
        </div>

        {[
          { to: "/", label: "Dashboard" },
          { to: "/trades", label: "Trades" },
          { to: "/signals", label: "Signals" },
          { to: "/analytics", label: "Analytics" },
          { to: "/events", label: "Events" },
          { to: "/settings", label: "Settings" },
        ].map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `text-sm font-medium transition-colors ${
                isActive ? "text-green-400" : "text-gray-400 hover:text-gray-200"
              }`
            }
          >
            {label}
          </NavLink>
        ))}

        {/* WS status indicator */}
        <div className="ml-auto flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
          <span className="text-xs text-gray-500">{connected ? "Live" : "Disconnected"}</span>
        </div>
      </nav>

      {/* Page content */}
      <main className="flex-1 p-6">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/signals" element={<SignalsPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </main>
    </div>
  );
}
