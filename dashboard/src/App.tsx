import { Routes, Route, NavLink } from "react-router-dom";
import { useWebSocket } from "./hooks/useWebSocket";
import MarketClock from "./components/MarketClock";
import DashboardPage from "./pages/Dashboard";
import TradesPage from "./pages/Trades";
import AnalyticsPage from "./pages/Analytics";
import SignalsPage from "./pages/Signals";
import EventsPage from "./pages/Events";
import SettingsPage from "./pages/Settings";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard" },
  { to: "/trades", label: "Trades" },
  { to: "/signals", label: "Signals" },
  { to: "/analytics", label: "Analytics" },
  { to: "/events", label: "Events" },
  { to: "/settings", label: "Settings" },
];

export default function App() {
  const { connected } = useWebSocket();

  return (
    <div className="min-h-screen flex flex-col">
      {/* Top nav — wraps cleanly on phones; clock + Live on its own row when needed */}
      <nav className="bg-gray-900 border-b border-gray-800 px-3 sm:px-6 py-3 sticky top-0 z-20 backdrop-blur-sm">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {/* Brand */}
          <div className="flex items-center gap-2 mr-2">
            <span className="text-lg sm:text-xl font-bold text-green-400 font-mono">TradingBot</span>
            <span className="text-gray-600 text-xs sm:text-sm">v2</span>
          </div>

          {/* Nav links — horizontally scrollable on small screens */}
          <div className="flex items-center gap-3 sm:gap-5 overflow-x-auto -mx-1 px-1 flex-1 sm:flex-none">
            {NAV_ITEMS.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors whitespace-nowrap py-1 ${
                    isActive ? "text-green-400" : "text-gray-400 hover:text-gray-200"
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </div>

          {/* Market clock + WS status */}
          <div className="flex items-center gap-3 sm:gap-4 sm:ml-auto w-full sm:w-auto justify-between sm:justify-end">
            <MarketClock />
            <div className="flex items-center gap-2 sm:pl-4 sm:border-l border-gray-800">
              <div className={`w-2 h-2 rounded-full ${connected ? "bg-green-400 animate-pulse" : "bg-red-500"}`} />
              <span className="text-xs text-gray-500">{connected ? "Live" : "Off"}</span>
            </div>
          </div>
        </div>
      </nav>

      {/* Page content */}
      <main className="flex-1 p-3 sm:p-6">
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
