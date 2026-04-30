export interface Trade {
  id: string;
  symbol: string;
  setup: string;
  qty: number;
  entryPrice: number;
  exitPrice?: number;
  stopPrice: number;
  targetPrice: number;
  realizedPnl?: number;
  pnlPct?: number;
  holdMinutes?: number;
  exitReason?: string;
  status: "OPEN" | "CLOSED" | "CANCELLED";
  entryTime: string;
  exitTime?: string;
  rrRatio: number;
  entryRvol?: number;
}

export interface Position {
  id: string;
  symbol: string;
  qty: number;
  entryPrice: number;
  currentPrice?: number;
  stopPrice: number;
  targetPrice: number;
  unrealizedPnl?: number;
  setup?: string;
  entryTime: string;
}

export interface TradingSession {
  id: string;
  date: string;
  startedAt: string;
  endedAt?: string;
  startingEquity: number;
  endingEquity?: number;
  realizedPnl: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  accuracyPct?: number;
  avgWinner?: number;
  avgLoser?: number;
  halted: boolean;
  haltReason?: string;
  tradingMode: string;
}

export interface Signal {
  id: string;
  symbol: string;
  setup: string;
  confidence: string;
  timestamp: string;
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  rrRatio: number;
  acted: boolean;
  rejectionReason?: string;
  vwap?: number;
  macdLine?: number;
  rvolAtSignal?: number;
  price?: number;
  pctChange?: number;
}

export interface ScanCandidate {
  symbol: string;
  price: number;
  pctChange: number;
  relativeVolume: number;
  floatShares?: number;
  hasNews: boolean;
  score: number;
  passedFilters: boolean;
  rank?: number;
}

export interface BotEvent {
  id: string;
  eventType: string;
  severity: string;
  message: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface EquitySnapshot {
  equity: number;
  dayPnl?: number;
  timestamp: string;
}

export interface PerformanceSummary {
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  accuracyPct: number;
  totalPnl: number;
  avgWinner: number;
  avgLoser: number;
  profitFactor: number;
}

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

export interface GateCheck {
  symbol: string;
  gates: Record<string, boolean>;
  setup: string | null;
  confidence: string | null;
  ts: string;
  receivedAt: number;
}

export interface WsMessage {
  type: WsEventType;
  data: unknown;
  ts: number;
}
