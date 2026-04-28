# Trading-Bot-v2

Fully automated momentum day trading bot based on the DaytradeWarrior (Ross Cameron) methodology.

**Stack:** Python 3.11 · Node.js + Express + Prisma · React + Vite + Tailwind · PostgreSQL 16 · Docker · Raspberry Pi 5

**Dashboard:** `http://192.168.0.12:3200`

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/YOUR_USER/Trading-Bot-v2.git
cd Trading-Bot-v2

# 2. Configure
cp .env.example .env
# Fill in ALPACA_API_KEY, ALPACA_SECRET_KEY, POSTGRES_PASSWORD

# 3. Run (Pi 5 or any Docker host)
docker compose up --build -d
```

Open `http://192.168.0.12:3200` — the dashboard is live.

---

## Architecture

```
nginx:3200
  ├── /        → React Dashboard
  ├── /api/*   → Node.js API (Express + Prisma + PostgreSQL)
  └── /ws      → WebSocket (real-time updates)

Python Bot → POST /telemetry/* → API → PostgreSQL + WS broadcast
```

## Trading Rules (Encoded in Code)

- **Scanner:** price $2–$20, rel-vol ≥ 5×, %change ≥ 10%, float < 20M, news catalyst
- **Entry setups:** Micro pullback or Bull flag (A-quality only)
- **Gates:** MACD > 0, price > VWAP, volume surge ≥ 1.5×, time 7:00–11:00 AM ET
- **Risk:** 1% per trade, 2:1 R:R minimum, 2% max daily loss → auto-halt
- **Exit:** Bracket order (hard stop + target) + soft exits (VWAP breach, MACD cross, topping tail)

## ⚠️ Disclaimer

Run in **PAPER mode only** until 100+ trades show positive expectancy. Most day traders lose money.

## Documentation

See [`docs/Trading-Bot-v2-Specification.pdf`](docs/Trading-Bot-v2-Specification.pdf) for the full system specification.
